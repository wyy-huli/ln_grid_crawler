# 数据库读写接口
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.event import listen
from .models import Base, ApiConfig, FetchBatch, TimeSeriesData, UnitStatus, MeteringQuery, FetchFailureLog, ContractBasic, ContractDailyData
from utils.config import DATABASE_URL, is_mysql
from utils.logger import logger
from datetime import datetime, date
import json

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine)

if not is_mysql():
    def _enable_sqlite_foreign_keys(dbapi_con, connection_rec):
        dbapi_con.execute("PRAGMA foreign_keys=ON;")
    listen(engine, 'connect', _enable_sqlite_foreign_keys)

def _upsert(table_obj, session, values_list, index_elements, set_fields):
    """通用 upsert:MySQL 用 ON DUPLICATE KEY UPDATE,SQLite 用 ON CONFLICT。

    Args:
        table_obj: ORM 模型类
        session: 当前 session
        values_list: list of dict, 要插入的行数据
        index_elements: list[str], 冲突判断的索引字段
        set_fields: list[str], 冲突时更新的字段名
    """
    if not values_list:
        return
    if is_mysql():
        stmt = mysql_insert(table_obj).values(values_list)
        update_dict = {f: getattr(stmt.inserted, f) for f in set_fields}
        stmt = stmt.on_duplicate_key_update(**update_dict)
    else:
        stmt = sqlite_insert(table_obj).values(values_list)
        update_dict = {f: getattr(stmt.excluded, f) for f in set_fields}
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_=update_dict
        )
    session.execute(stmt)

def init_db():
    logger.info(f"[DB] 初始化数据库连接: {DATABASE_URL}")
    logger.info(f"[DB] 数据库类型: {'MySQL' if is_mysql() else 'SQLite'}")
    Base.metadata.create_all(engine)
    _migrate_db()
    logger.info("[DB] 数据库初始化完成")

def _migrate_v3_cleanup_temp_tables(session):
    """迁移 v3: 清理残留的临时表和备份表。"""
    logger.info("[DB迁移] 执行迁移 v3: 清理残留临时表")
    temp_tables = session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%_temp' OR name LIKE '%_backup');")
    ).fetchall()
    for (tbl_name,) in temp_tables:
        try:
            session.execute(text(f"DROP TABLE {tbl_name};"))
            logger.info(f"[DB迁移] 删除残留表: {tbl_name}")
        except Exception as e:
            logger.warning(f"[DB迁移] 删除 {tbl_name} 失败: {e}")
    logger.info("[DB迁移] 迁移 v3 完成")

def _migrate_db():
    """执行数据库迁移，支持 MySQL 和 SQLite"""
    session = SessionLocal()
    try:
        current_version = _get_migration_version(session)
        
        if current_version < 1:
            logger.info("[DB迁移] 执行迁移 v1: 为 metering_query 表添加 mname 列")
            try:
                session.execute(text("ALTER TABLE metering_query ADD COLUMN mname VARCHAR(100);"))
                session.commit()
                logger.info("[DB迁移] 迁移 v1 完成")
                current_version = 1
            except Exception as e:
                err_msg = str(e).lower()
                if "duplicate column name" in err_msg or "duplicate" in err_msg:
                    logger.info("[DB迁移] mname 列已存在，跳过")
                    current_version = 1
                else:
                    raise
        
        if current_version < 2 and not is_mysql():
            _migrate_v2_fix_autoincrement(session)
            current_version = 2

        if current_version < 3 and not is_mysql():
            _migrate_v3_cleanup_temp_tables(session)
            current_version = 3
        
        _update_migration_version(session, current_version)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"[DB迁移] 迁移失败: {e}")
    finally:
        session.close()

def _migrate_v2_fix_autoincrement(session):
    """迁移 v2: 修复 SQLite 表缺少 AUTOINCREMENT 的问题。
    
    在 SQLite 中，Integer PRIMARY KEY 不会自动生成 ID，必须显式指定 AUTOINCREMENT。
    由于 ALTER TABLE 无法添加 AUTOINCREMENT，需要重建表并迁移数据。
    
    注意：重建过程中临时禁用外键检查，避免 ALTER TABLE RENAME 后外键约束引用失效。
    """
    logger.info("[DB迁移] 执行迁移 v2: 修复 SQLite AUTOINCREMENT 问题")
    
    # 临时禁用外键检查（重建表时避免外键约束冲突）
    session.execute(text("PRAGMA foreign_keys=OFF;"))
    
    # 重建顺序：先父表，后子表（避免外键约束引用已删除的临时表）
    tables_to_fix = [
        ('api_config', 'api_id'),           # 父表：被 fetch_batch 引用
        ('fetch_batch', 'batch_id'),        # 子表：引用 api_config
        ('time_series_data', 'data_id'),    # 子表：引用 fetch_batch
        ('contract_daily_data', 'id'),      # 子表：引用 contract_basic
        ('unit_status', 'id'),              # 无引用
        ('metering_query', 'id'),           # 无引用
        ('fetch_failure_log', 'id'),        # 无引用
    ]
    
    for table_name, pk_name in tables_to_fix:
        try:
            needs_rebuild = False
            
            # 检查 1: 表是否存在
            table_exists = session.execute(
                text(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table_name}';")
            ).fetchone()
            if not table_exists:
                logger.info(f"[DB迁移] {table_name} 不存在，跳过")
                continue

            # 检查 2: 缺少 AUTOINCREMENT
            has_autoincrement = session.execute(
                text(f"SELECT * FROM sqlite_master WHERE name='{table_name}' AND sql LIKE '%AUTOINCREMENT%';")
            ).fetchone()
            if not has_autoincrement:
                needs_rebuild = True
                logger.info(f"[DB迁移] {table_name} 缺少 AUTOINCREMENT")

            # 检查 3: 外键约束引用了临时表或不存在的表
            fks = session.execute(text(f"PRAGMA foreign_key_list({table_name});")).fetchall()
            for fk in fks:
                parent_table = fk[2]
                if '_temp' in parent_table or '_backup' in parent_table:
                    needs_rebuild = True
                    logger.info(f"[DB迁移] {table_name} 外键引用了临时/备份表 {parent_table}")
                    break
                parent_exists = session.execute(
                    text(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{parent_table}';")
                ).fetchone()
                if not parent_exists:
                    needs_rebuild = True
                    logger.info(f"[DB迁移] {table_name} 外键引用了不存在的表 {parent_table}")
                    break

            if needs_rebuild:
                logger.info(f"[DB迁移] 重建表 {table_name}")
                _recreate_table_with_autoincrement(session, table_name)
            else:
                logger.info(f"[DB迁移] {table_name} 结构正常，跳过")
        except Exception as e:
            logger.warning(f"[DB迁移] 检查 {table_name} 时出错: {e}")

    # 重新启用外键检查
    session.execute(text("PRAGMA foreign_keys=ON;"))
    logger.info("[DB迁移] 迁移 v2 完成")

# SQLite 表定义（用于重建）
_SQLITE_TABLE_DEFS = {
    'api_config': """
        CREATE TABLE api_config (
            api_id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_code VARCHAR(50) UNIQUE NOT NULL,
            api_name VARCHAR(100) NOT NULL,
            fetch_type VARCHAR(10) DEFAULT 'type1',
            fetch_freq VARCHAR(20) DEFAULT '1d',
            is_active INTEGER DEFAULT 1,
            created_at DATETIME,
            updated_at DATETIME
        );
    """,
    'fetch_batch': """
        CREATE TABLE fetch_batch (
            batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_id INTEGER NOT NULL,
            target_date DATE NOT NULL,
            fetch_time DATETIME,
            is_latest INTEGER DEFAULT 1,
            FOREIGN KEY (api_id) REFERENCES api_config(api_id)
        );
    """,
    'time_series_data': """
        CREATE TABLE time_series_data (
            data_id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            time_point VARCHAR(5) NOT NULL,
            value REAL NOT NULL,
            FOREIGN KEY (batch_id) REFERENCES fetch_batch(batch_id) ON DELETE CASCADE,
            UNIQUE (batch_id, time_point)
        );
    """,
    'unit_status': """
        CREATE TABLE unit_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_time VARCHAR(8) NOT NULL,
            unit_name VARCHAR(100) NOT NULL,
            unit_number VARCHAR(50),
            capacity VARCHAR(20),
            status VARCHAR(20),
            cause VARCHAR(100),
            apply_id VARCHAR(64),
            guid VARCHAR(64) UNIQUE NOT NULL,
            created_at DATETIME
        );
    """,
    'metering_query': """
        CREATE TABLE metering_query (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_date DATE NOT NULL,
            cons_no VARCHAR(50) NOT NULL,
            mid VARCHAR(64) NOT NULL,
            mname VARCHAR(100),
            response_json TEXT,
            created_at DATETIME
        );
    """,
    'fetch_failure_log': """
        CREATE TABLE fetch_failure_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_code VARCHAR(50) NOT NULL,
            target_time DATETIME NOT NULL,
            reason VARCHAR(255),
            created_at DATETIME
        );
    """,
    'contract_daily_data': """
        CREATE TABLE contract_daily_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id VARCHAR(64) NOT NULL,
            curve_date DATE NOT NULL,
            time_point VARCHAR(5) NOT NULL,
            electricity REAL,
            price REAL,
            created_at DATETIME,
            FOREIGN KEY (contract_id) REFERENCES contract_basic(contract_id) ON DELETE CASCADE,
            UNIQUE (contract_id, curve_date, time_point)
        );
    """,
}

def _recreate_table_with_autoincrement(session, table_name):
    """重建 SQLite 表并添加 AUTOINCREMENT，保留现有数据。
    
    使用 CREATE TABLE ... AS SELECT 创建备份，避免 ALTER TABLE RENAME
    导致的外键约束引用临时表名问题。
    """
    backup_name = f"{table_name}_backup"
    
    if table_name not in _SQLITE_TABLE_DEFS:
        logger.info(f"[DB迁移] 跳过未知表 {table_name}")
        return

    try:
        # 1. 创建备份表（只复制数据，不复制约束）
        session.execute(text(f"CREATE TABLE {backup_name} AS SELECT * FROM {table_name};"))

        # 2. 获取备份表的列名
        cols_result = session.execute(text(f"PRAGMA table_info({backup_name});")).fetchall()
        cols = [col[1] for col in cols_result]
        col_list = ', '.join(cols)

        # 3. 删除旧表（外键检查已禁用）
        session.execute(text(f"DROP TABLE {table_name};"))

        # 4. 创建新表（带 AUTOINCREMENT）
        session.execute(text(_SQLITE_TABLE_DEFS[table_name]))

        # 5. 从备份表恢复数据
        session.execute(text(f"INSERT INTO {table_name} ({col_list}) SELECT {col_list} FROM {backup_name};"))

        # 6. 删除备份表
        session.execute(text(f"DROP TABLE {backup_name};"))

        session.commit()
        logger.info(f"[DB迁移] 成功重建 {table_name}")
    except Exception as e:
        session.rollback()
        # 清理残留
        session.execute(text(f"DROP TABLE IF EXISTS {backup_name};"))
        session.commit()
        logger.error(f"[DB迁移] 重建 {table_name} 失败: {e}")
        raise

def _get_migration_version(session):
    """获取当前数据库迁移版本"""
    try:
        result = session.execute(text("SELECT version FROM migration_version LIMIT 1;")).fetchone()
        return result[0] if result else 0
    except Exception:
        return 0

def _update_migration_version(session, version):
    """更新数据库迁移版本，兼容 MySQL 和 SQLite。

    1. 先 CREATE TABLE IF NOT EXISTS（建表幂等）
    2. 再执行 upsert 写入版本号
    """
    # 1. 确保 migration_version 表存在
    session.execute(text(
        "CREATE TABLE IF NOT EXISTS migration_version (version INTEGER PRIMARY KEY);"
    ))

    # 2. upsert 写入版本
    if is_mysql():
        session.execute(text("""
            INSERT INTO migration_version (version) VALUES (:version)
            ON DUPLICATE KEY UPDATE version = :version;
        """), {"version": version})
    else:
        session.execute(text(
            "INSERT OR REPLACE INTO migration_version (version) VALUES (:version);"
        ), {"version": version})

def get_or_create_api(api_code, api_name, fetch_type='type1', session=None):
    """获取或创建接口配置。

    Args:
        session: 可选，复用外部 session 以减少连接开销；
                不传则内部自管 session（向后兼容）
    """
    own_session = session is None
    if own_session:
        session = SessionLocal()
    try:
        api = session.query(ApiConfig).filter_by(api_code=api_code).first()
        if not api:
            api = ApiConfig(api_code=api_code, api_name=api_name, fetch_type=fetch_type)
            session.add(api)
            session.commit()
            session.refresh(api)
        else:
            if api.api_name != api_name or api.fetch_type != fetch_type:
                api.api_name = api_name
                api.fetch_type = fetch_type
                session.commit()
        return api.api_id
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()

def save_type1_batch(api_code, api_name, target_date_str, records):
    """records: [{'x':'00:15', 'y':25730.48}, ...]"""
    if not records: return
    session = SessionLocal()
    try:
        api_id = get_or_create_api(api_code, api_name, 'type1')
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        # 旧批次置非最新
        session.query(FetchBatch).filter(
            FetchBatch.api_id == api_id,
            FetchBatch.target_date == target_date,
            FetchBatch.is_latest == 1
        ).update({'is_latest': 0})
        # 新批次
        batch = FetchBatch(api_id=api_id, target_date=target_date, is_latest=1)
        session.add(batch)
        session.flush()
        for r in records:
            session.add(TimeSeriesData(batch_id=batch.batch_id, time_point=r['x'], value=float(r['y'])))
        session.commit()
        logger.info(f"[DB] 保存 {api_name} {len(records)} 条")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

def upsert_type2_data(api_code, api_name, target_date_str, records):
    """类型2：逐点更新，每天只保留一个批次，每次抓取覆盖对应时间点"""
    if not records:
        logger.info(f"[DB] {api_name} 无数据，跳过存储")
        return

    session = SessionLocal()
    try:
        api_id = get_or_create_api(api_code, api_name, 'type2')
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()

        # 获取或创建当天唯一批次
        batch = session.query(FetchBatch).filter(
            FetchBatch.api_id == api_id,
            FetchBatch.target_date == target_date,
            FetchBatch.is_latest == 1
        ).first()
        if not batch:
            batch = FetchBatch(api_id=api_id, target_date=target_date, is_latest=1)
            session.add(batch)
            session.flush()

        updated_points = []
        rows = []
        for r in records:
            tp = r['x'][:5]
            value = float(r['y'])
            rows.append({
                'batch_id': batch.batch_id,
                'time_point': tp,
                'value': value
            })
            updated_points.append(tp)

        _upsert(
            TimeSeriesData, session, rows,
            index_elements=['batch_id', 'time_point'],
            set_fields=['value']
        )

        session.commit()
        logger.info(f"[DB] upsert {api_name} {len(records)} 条，时间点: {updated_points[:3]}...")
        count = session.execute(
            text("SELECT COUNT(*) FROM time_series_data WHERE batch_id = :bid"),
            {"bid": batch.batch_id}
        ).scalar()
        logger.info(f"[DB] 批次 {batch.batch_id} 当前总点数: {count}")
    except Exception as e:
        session.rollback()
        logger.error(f"[DB] upsert失败: {e}")
        raise e
    finally:
        session.close()

def save_type4_data(records):
    """records: list of dict from objectList"""
    if not records: return
    session = SessionLocal()
    try:
        # 1. 批量查询已存在的 guid
        guids = [item.get('guid') for item in records if item.get('guid')]
        if not guids:
            logger.info("[DB] save_type4_data 无有效 guid，跳过")
            return
        existing_guids = set(
            gid for (gid,) in session.query(UnitStatus.guid).filter(UnitStatus.guid.in_(guids)).all()
        )

        # 2. 批量构造新行（去重 + 过滤已存在）
        seen = set()
        rows = []
        for item in records:
            guid = item.get('guid')
            if not guid or guid in existing_guids or guid in seen:
                continue
            seen.add(guid)
            rows.append({
                'business_time': item.get('businessTime'),
                'unit_name': item.get('name'),
                'unit_number': item.get('number'),
                'capacity': item.get('volume'),
                'status': item.get('item'),
                'cause': item.get('cause'),
                'apply_id': item.get('applyId'),
                'guid': guid,
                'created_at': datetime.now(),
            })

        if rows:
            _upsert(
                UnitStatus, session, rows,
                index_elements=['guid'],
                set_fields=['business_time', 'unit_name', 'unit_number',
                            'capacity', 'status', 'cause', 'apply_id']
            )
            session.commit()
        logger.info(f"[DB] 保存机组状态 {len(rows)} 条（输入 {len(records)} 条）")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

def save_type3_query(query_date, cons_no, mid, response_json, mname=None):
    session = SessionLocal()
    try:
        if isinstance(query_date, str):
            query_date = datetime.strptime(query_date, '%Y-%m-%d').date()
        cons_no = str(cons_no).strip() if cons_no else ''
        mid = str(mid).strip() if mid else ''
        mname = str(mname).strip() if mname else None
        if not cons_no or not mid:
            raise ValueError(f"参数无效: cons_no='{cons_no}', mid='{mid}'")
        session.add(MeteringQuery(query_date=query_date, cons_no=cons_no, mid=mid, mname=mname, response_json=response_json))
        session.commit()
        logger.info(f"[DB] 保存用电查询: {query_date} {cons_no} {'(' + mname + ')' if mname else ''}")
    except Exception as e:
        session.rollback()
        logger.error(f"[DB] 保存失败: {e}")
        raise e
    finally:
        session.close()

def log_failure(api_code, reason):
    """记录失败日志。自身抛出的任何异常都会被吞掉，避免影响调度链路。"""
    session = None
    try:
        session = SessionLocal()
        session.add(FetchFailureLog(api_code=api_code, target_time=datetime.now(), reason=reason))
        session.commit()
    except Exception as e:
        # 数据库锁/异常不能影响调度，仅打印
        logger.error(f"[DB] log_failure 失败（吞掉）: {e}")
    finally:
        try:
            if session is not None:
                session.close()
        except Exception:
            pass


def upsert_contract_basic(contract_data):
    session = SessionLocal()
    try:
        contract_id = contract_data['contract_id']
        existing = session.query(ContractBasic).filter_by(contract_id=contract_id).first()
        
        if existing:
            existing.contract_name = contract_data.get('contract_name', existing.contract_name)
            existing.seller = contract_data.get('seller', existing.seller)
            existing.buyer = contract_data.get('buyer', existing.buyer)
            existing.contract_type = contract_data.get('contract_type', existing.contract_type)
            existing.contract_sequence = contract_data.get('contract_sequence', existing.contract_sequence)
            existing.contract_electricity = contract_data.get('contract_electricity', existing.contract_electricity)
            existing.monthly_electricity = contract_data.get('monthly_electricity', existing.monthly_electricity)
            existing.monthly_price = contract_data.get('monthly_price', existing.monthly_price)
            existing.curve_status = contract_data.get('curve_status', existing.curve_status)
            existing.settlement_point = contract_data.get('settlement_point', existing.settlement_point)
        else:
            session.add(ContractBasic(
                contract_id=contract_id,
                contract_name=contract_data.get('contract_name', ''),
                seller=contract_data.get('seller'),
                buyer=contract_data.get('buyer'),
                contract_type=contract_data.get('contract_type'),
                contract_sequence=contract_data.get('contract_sequence'),
                contract_electricity=contract_data.get('contract_electricity'),
                monthly_electricity=contract_data.get('monthly_electricity'),
                monthly_price=contract_data.get('monthly_price'),
                curve_status=contract_data.get('curve_status'),
                settlement_point=contract_data.get('settlement_point'),
            ))
        session.commit()
        logger.info(f"[DB] 保存合同基础信息: {contract_id}")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def save_contract_daily_data(contract_id, curve_date_str, electricity_data, price_data, log_callback=None):
    session = SessionLocal()
    try:
        curve_date = datetime.strptime(curve_date_str, '%Y-%m-%d').date()
        
        elec_rows = []
        for tp, electricity in electricity_data.items():
            elec_rows.append({
                'contract_id': contract_id,
                'curve_date': curve_date,
                'time_point': tp,
                'electricity': float(electricity),
                'price': None
            })
        _upsert(
            ContractDailyData, session, elec_rows,
            index_elements=['contract_id', 'curve_date', 'time_point'],
            set_fields=['electricity']
        )

        price_rows = []
        for tp, price in price_data.items():
            price_rows.append({
                'contract_id': contract_id,
                'curve_date': curve_date,
                'time_point': tp,
                'electricity': None,
                'price': float(price)
            })
        _upsert(
            ContractDailyData, session, price_rows,
            index_elements=['contract_id', 'curve_date', 'time_point'],
            set_fields=['price']
        )
        
        session.commit()
        
        # 保存后验证
        if log_callback:
            records = session.query(ContractDailyData).filter_by(
                contract_id=contract_id,
                curve_date=curve_date
            ).all()
            non_zero_elec = sum(1 for r in records if r.electricity and r.electricity != 0)
            non_zero_price = sum(1 for r in records if r.price and r.price != 0)
            log_callback(f"  [DB验证] {curve_date_str} 共{len(records)}条，非零电量:{non_zero_elec}，非零电价:{non_zero_price}")
            if records:
                # 打印几个时间点的值
                sample_points = ['00:00', '08:00', '12:00', '15:00', '20:00']
                for sp in sample_points:
                    rec = session.query(ContractDailyData).filter_by(
                        contract_id=contract_id, curve_date=curve_date, time_point=sp
                    ).first()
                    if rec:
                        log_callback(f"    {sp}: 电量={rec.electricity}, 电价={rec.price}")
        
        logger.info(f"[DB] 保存合同日数据: {contract_id} {curve_date_str}")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

