# 数据库读写接口（强制使用远程 MySQL，不再支持 SQLite）
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.mysql import insert as mysql_insert
from .models import Base, ApiConfig, FetchBatch, TimeSeriesData, UnitStatus, MeteringQuery, FetchFailureLog, ContractBasic, ContractDailyData, Tenant, RetryState
from utils.config import _build_mysql_url, LEGACY_SQLITE_DB_FILE
from utils.logger import logger
from datetime import datetime, date, timedelta
import json
import threading

# ========== MySQL 连接探测（无任何 SQLite 回退） ==========

def _try_mysql_connect(url, timeout_sec=3):
    """尝试连接 MySQL，成功返回 engine，失败返回 None。

    Args:
        url: MySQL 连接 URL
        timeout_sec: 连接超时时间（秒）

    Returns:
        engine 成功 / None 失败
    """
    try:
        # 使用 connect_args 设置连接超时，避免长时间阻塞
        eng = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=3600,
            connect_args={'connect_timeout': timeout_sec},
        )
        # 真实执行一次连接，触发建连/鉴权
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception as e:
        logger.warning(f"[DB] MySQL 连接失败: {type(e).__name__}: {e}")
        try:
            eng.dispose()
        except Exception:
            pass
        return None

def _build_engine():
    """创建 MySQL 数据库 engine（强制远程 MySQL，不再有任何 SQLite 回退）。

    流程：
    1. 调用 _build_mysql_url() 获取 URL（内部若未配置会抛 RuntimeError）
    2. 尝试连接 MySQL，3 秒超时
    3. 连接失败抛出 ConnectionError（异常中含脱敏的 host/db 信息）
    4. 连接成功返回 engine

    任何阶段失败都不会"静默继续"，确保"数据库不可达时程序明确报错退出"。
    """
    mysql_url = _build_mysql_url()
    # 从 URL 中提取 host 用于日志和错误信息（密码脱敏）
    try:
        from sqlalchemy.engine.url import make_url
        u = make_url(mysql_url)
        log_host = f"{u.host or '?'}:{u.port or 3306}/{u.database or '?'}"
    except Exception:
        log_host = "(解析失败)"
    logger.info(f"[DB] 连接远程 MySQL: {log_host} ...")

    eng = _try_mysql_connect(mysql_url, timeout_sec=3)
    if eng is not None:
        logger.info(f"[DB] MySQL 连接成功: {log_host}")
        return eng

    raise ConnectionError(
        f"[DB] 远程 MySQL 连接失败 ({log_host})。\n"
        f"请检查：\n"
        f"  1. MySQL 服务是否运行\n"
        f"  2. .env 中的 GRID_DB_HOST / GRID_DB_PORT / GRID_DB_USER / GRID_DB_PASSWORD 是否正确\n"
        f"  3. 服务器防火墙/安全组是否放行 3306 端口\n"
        f"  4. MySQL 是否允许当前客户端 IP 连接"
    )


def is_mysql():
    """判断当前是否使用 MySQL 数据库（强制 MySQL 版本，恒返回 True，保留接口兼容）。"""
    return True


# Q2-1 修复：engine lazy 加载，避免子进程 import 时立即连接 MySQL（节省 1-3s 冷启动时间）
# 主进程首次访问 engine 时才真正建连；子进程 import db_manager 时仅创建对象不连接
_engine_instance = None
_engine_lock = threading.Lock()


def get_engine():
    """获取 engine 单例（lazy 加载，线程安全）。

    主进程首次调用时建连，子进程 import 时不会触发连接。
    替代原 `engine = _build_engine()` 的模块级立即加载。
    """
    global _engine_instance
    if _engine_instance is not None:
        return _engine_instance
    with _engine_lock:
        # 双重检查，避免多线程下重复建连
        if _engine_instance is not None:
            return _engine_instance
        _engine_instance = _build_engine()
        return _engine_instance


class _LazyEngineProxy:
    """engine 的 lazy 代理：兼容 `from db_manager import engine` 的旧代码。

    访问任何属性时自动触发 get_engine() 建连，实现"用到才连接"。
    """
    def __getattr__(self, name):
        return getattr(get_engine(), name)

    def __repr__(self):
        if _engine_instance is not None:
            return repr(_engine_instance)
        return "<LazyEngine (not yet connected)>"


# 向后兼容：保留 engine 全局变量，但改为 lazy 代理
engine = _LazyEngineProxy()


class _LazySessionLocal:
    """SessionLocal 的 lazy 代理：首次调用时才 bind engine。

    避免模块加载时 `sessionmaker(bind=engine)` 触发 engine 建连。
    """
    def __init__(self):
        self._factory = None

    def _ensure_factory(self):
        if self._factory is None:
            self._factory = sessionmaker(bind=get_engine())
        return self._factory

    def __call__(self, *args, **kwargs):
        return self._ensure_factory()(*args, **kwargs)

    def configure(self, **kwargs):
        self._ensure_factory().configure(**kwargs)


SessionLocal = _LazySessionLocal()

def _upsert(table_obj, session, values_list, index_elements, set_fields):
    """通用 upsert（MySQL ON DUPLICATE KEY UPDATE，强制 MySQL 版本）。

    Args:
        table_obj: ORM 模型类
        session: 当前 session
        values_list: list of dict, 要插入的行数据
        index_elements: list[str], 冲突判断的索引字段（MySQL 下通过 UNIQUE 索引自动识别）
        set_fields: list[str], 冲突时更新的字段名
    """
    if not values_list:
        return
    stmt = mysql_insert(table_obj).values(values_list)
    update_dict = {f: getattr(stmt.inserted, f) for f in set_fields}
    stmt = stmt.on_duplicate_key_update(**update_dict)
    session.execute(stmt)

def init_db():
    """初始化数据库（强制远程 MySQL，配置缺失或连接失败会抛异常，由上层弹窗提示）。

    lazy 建连流程：此处首次调用 get_engine() 触发真实 MySQL 连接，
    确保连接成功后再建表和执行迁移。
    """
    # logger 就绪后输出 .env 加载情况（避免循环导入）
    try:
        from utils.config import _env_load_results
        for env_path, count, err in _env_load_results:
            if err is None:
                logger.info(f"[CONFIG] 已加载 .env: {env_path} ({count} 项)")
            else:
                logger.error(f"[CONFIG] 加载 .env 文件失败 ({env_path}): {err}")
    except Exception:
        pass

    # 检测旧版 SQLite 数据文件，存在则日志提醒（不主动删除，避免误删用户数据）
    try:
        import os as _os
        if _os.path.isfile(LEGACY_SQLITE_DB_FILE):
            size_mb = _os.path.getsize(LEGACY_SQLITE_DB_FILE) / (1024 * 1024)
            logger.warning(
                f"[DB] 检测到旧版 SQLite 数据文件: {LEGACY_SQLITE_DB_FILE} "
                f"(约 {size_mb:.1f} MB)。当前版本已强制切换为远程 MySQL，"
                "该文件不再使用，如需保留历史数据请手动迁移，否则可删除以释放磁盘空间。"
            )
    except Exception:
        pass

    # 显式触发 lazy 建连（配置缺失或连接失败都会抛异常）
    real_engine = get_engine()
    logger.info(f"[DB] 初始化数据库连接: {real_engine.url}")
    logger.info("[DB] 数据库类型: MySQL")
    Base.metadata.create_all(real_engine)
    _migrate_db()
    logger.info("[DB] 数据库初始化完成")


def _migrate_v4_add_tenant(session):
    """迁移 v4: 新增租户表、为 metering_query/contract_basic 添加 dept_id 列"""
    logger.info("[DB迁移] 执行迁移 v4: 租户模型")

    # 1. 建 tenant 表
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS tenant (
            dept_id     VARCHAR(64)  PRIMARY KEY,
            dept_name   VARCHAR(200) NOT NULL,
            is_active   TINYINT      DEFAULT 1,
            created_at  DATETIME,
            updated_at  DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """))
    logger.info("[DB迁移] tenant 表创建完成")

    # 2. metering_query 加 dept_id
    try:
        session.execute(text(
            "ALTER TABLE metering_query ADD COLUMN dept_id VARCHAR(64) NULL COMMENT '租户ID'"
        ))
        logger.info("[DB迁移] metering_query.dept_id 列添加完成")
    except Exception as e:
        if 'Duplicate column' in str(e):
            logger.info("[DB迁移] metering_query.dept_id 列已存在，跳过")
        else:
            raise

    # 3. metering_query 唯一索引（MySQL 允许多个 NULL 共存，历史 dept_id=NULL 数据不会冲突）
    try:
        session.execute(text("""
            CREATE UNIQUE INDEX uq_metering_dept_cons_mid_date
                ON metering_query(dept_id, cons_no, mid, query_date);
        """))
        logger.info("[DB迁移] uq_metering_dept_cons_mid_date 索引创建完成")
    except Exception as e:
        if 'Duplicate' in str(e):
            logger.info("[DB迁移] uq_metering_dept_cons_mid_date 索引已存在，跳过")
        else:
            raise

    # 4. contract_basic 加 dept_id
    try:
        session.execute(text(
            "ALTER TABLE contract_basic ADD COLUMN dept_id VARCHAR(64) NULL COMMENT '租户ID'"
        ))
        logger.info("[DB迁移] contract_basic.dept_id 列添加完成")
    except Exception as e:
        if 'Duplicate column' in str(e):
            logger.info("[DB迁移] contract_basic.dept_id 列已存在，跳过")
        else:
            raise

    # 5. contract_basic 普通索引
    try:
        session.execute(text(
            "CREATE INDEX idx_contract_basic_dept ON contract_basic(dept_id);"
        ))
        logger.info("[DB迁移] idx_contract_basic_dept 索引创建完成")
    except Exception as e:
        if 'Duplicate' in str(e):
            logger.info("[DB迁移] idx_contract_basic_dept 索引已存在，跳过")
        else:
            raise

    # 6. 提交所有变更
    session.commit()
    logger.info("[DB迁移] 迁移 v4 完成")

def _migrate_v5_add_retry_state(session):
    """迁移 v5: 新增重试状态持久化表 retry_state（Q0-3 修复）。

    强制 MySQL 版本：Base.metadata.create_all 已在 init_db 中执行，
    会自动创建 retry_state 表，此处做额外幂等校验以兼容老库。
    """
    logger.info("[DB迁移] 执行迁移 v5: 重试状态持久化表 retry_state")
    try:
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS retry_state ("
            "  api_code VARCHAR(50) PRIMARY KEY,"
            "  retry_count SMALLINT NOT NULL DEFAULT 0,"
            "  last_exc_type VARCHAR(100),"
            "  last_exc_time DATETIME,"
            "  scheduled_next_time DATETIME,"
            "  job_id VARCHAR(100),"
            "  func_path VARCHAR(200),"
            "  timeout_sec INT,"
            "  args_json TEXT,"
            "  updated_at DATETIME"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
        ))
        session.commit()
        logger.info("[DB迁移] retry_state 表就绪，迁移 v5 完成")
    except Exception as e:
        session.rollback()
        # 表已存在等非致命错误，记录后继续
        logger.warning(f"[DB迁移] 迁移 v5 注意: {e}")

def _migrate_db():
    """执行数据库迁移（强制 MySQL 版本）。

    v1: metering_query 增加 mname 列（通用）
    v2: 已删除（仅 SQLite 专属 AUTOINCREMENT 修复，不再需要）
    v3: 已删除（仅 SQLite 专属临时表清理，不再需要）
    v4: 新增租户模型（MySQL，已去除 `and is_mysql()` 守卫，恒 True）
    v5: retry_state 表（通用）
    """
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

        if current_version < 4:
            # v2/v3 是 SQLite 专属迁移，直接跳过并推进版本号到 3
            if current_version < 3:
                current_version = 3
            _migrate_v4_add_tenant(session)
            current_version = 4

        if current_version < 5:
            _migrate_v5_add_retry_state(session)
            current_version = 5

        _update_migration_version(session, current_version)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"[DB迁移] 迁移失败: {e}")
    finally:
        session.close()

def _get_migration_version(session):
    """获取当前数据库迁移版本"""
    try:
        result = session.execute(text("SELECT version FROM migration_version LIMIT 1;")).fetchone()
        return result[0] if result else 0
    except Exception:
        return 0

def _update_migration_version(session, version):
    """更新数据库迁移版本（强制 MySQL，ON DUPLICATE KEY UPDATE）。

    1. 先 CREATE TABLE IF NOT EXISTS（建表幂等）
    2. 再执行 upsert 写入版本号
    """
    # 1. 确保 migration_version 表存在
    session.execute(text(
        "CREATE TABLE IF NOT EXISTS migration_version (version INTEGER PRIMARY KEY);"
    ))

    # 2. upsert 写入版本（MySQL 专用）
    session.execute(text("""
        INSERT INTO migration_version (version) VALUES (:version)
        ON DUPLICATE KEY UPDATE version = :version;
    """), {"version": version})

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
            # flush 获取自增 ID，但不 commit 事务
            session.flush()
            if own_session:
                session.commit()
        else:
            if api.api_name != api_name or api.fetch_type != fetch_type:
                api.api_name = api_name
                api.fetch_type = fetch_type
                if own_session:
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
    if not records:
        return
    session = SessionLocal()
    try:
        # 复用 session，避免额外创建连接
        api_id = get_or_create_api(api_code, api_name, 'type1', session=session)
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
        # 复用 session，避免额外创建连接
        api_id = get_or_create_api(api_code, api_name, 'type2', session=session)
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
        # 1. 批量查询已存在的 guid（仅用于日志统计，不影响 upsert）
        guids = [item.get('guid') for item in records if item.get('guid')]
        if not guids:
            logger.info("[DB] save_type4_data 无有效 guid，跳过")
            return
        existing_guids = set(
            gid for (gid,) in session.query(UnitStatus.guid).filter(UnitStatus.guid.in_(guids)).all()
        )

        # 2. 批量构造新行（去重），交由 _upsert 处理新增/更新
        seen = set()
        rows = []
        for item in records:
            guid = item.get('guid')
            if not guid or guid in seen:
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
        updated_count = len(existing_guids & set(seen))
        logger.info(f"[DB] 保存机组状态 {len(rows)} 条（输入 {len(records)} 条，新增 {len(rows) - updated_count} 条，更新 {updated_count} 条）")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

def save_type3_query(query_date, cons_no, mid, response_json, mname=None, dept_id=None):
    session = SessionLocal()
    try:
        if isinstance(query_date, str):
            query_date = datetime.strptime(query_date, '%Y-%m-%d').date()
        cons_no = str(cons_no).strip() if cons_no else ''
        mid = str(mid).strip() if mid else ''
        mname = str(mname).strip() if mname else None
        if not cons_no or not mid:
            raise ValueError(f"参数无效: cons_no='{cons_no}', mid='{mid}'")

        # 'UNKNOWN' 视为 None，避免写入脏数据
        if dept_id == 'UNKNOWN':
            dept_id = None

        values = {
            'dept_id': dept_id,
            'query_date': query_date,
            'cons_no': cons_no,
            'mid': mid,
            'mname': mname,
            'response_json': response_json,
        }
        _upsert(
            MeteringQuery, session, [values],
            index_elements=['dept_id', 'cons_no', 'mid', 'query_date'],
            set_fields=['response_json', 'mname']
        )
        session.commit()
        logger.info(f"[DB] upsert用电查询: dept={dept_id} {query_date} {cons_no} {'(' + mname + ')' if mname else ''}")
    except Exception as e:
        session.rollback()
        logger.error(f"[DB] upsert失败: {e}")
        raise e
    finally:
        session.close()

def log_failure(api_code, reason):
    """记录失败日志。自身抛出的任何异常都会被吞掉，避免影响调度链路。

    修复：reason 字段最长 255 字符，Playwright 异常含完整 URL 轻易超长，
    入库前截断到 200 字符（留余量），加 '...' 后缀，保证日志一定能入库。
    """
    session = None
    try:
        # 截断超长 reason，避免 "Data too long for column 'reason'" 入库失败
        REASON_MAX_LEN = 200
        if reason and len(reason) > REASON_MAX_LEN:
            reason = reason[:REASON_MAX_LEN] + '...'
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


def cleanup_failure_logs(days_to_keep=30):
    """清理超过指定天数的失败日志（Q1-7 修复）。

    Args:
        days_to_keep: 保留最近 N 天的日志，默认 30 天

    Returns:
        int: 删除的记录数（异常时返回 0）
    """
    session = None
    try:
        session = SessionLocal()
        cutoff = datetime.now() - timedelta(days=days_to_keep)
        # 先统计待删除数量
        count = session.query(FetchFailureLog).filter(
            FetchFailureLog.created_at < cutoff
        ).count()
        if count == 0:
            logger.info(f"[DB] 无需清理失败日志（保留 {days_to_keep} 天内）")
            return 0
        # 删除过期日志
        session.query(FetchFailureLog).filter(
            FetchFailureLog.created_at < cutoff
        ).delete(synchronize_session='fetch')
        session.commit()
        logger.info(f"[DB] 已清理 {count} 条过期失败日志（保留 {days_to_keep} 天内）")
        return count
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        logger.error(f"[DB] cleanup_failure_logs 失败（吞掉）: {e}")
        return 0
    finally:
        try:
            if session is not None:
                session.close()
        except Exception:
            pass


# ========== 重试状态持久化（Q0-3 修复） ==========

class RetryStateRepository:
    """重试状态仓储：封装 retry_state 表的增删改查，替代内存字典。

    线程安全说明：
    - 每个方法独立创建/关闭 session，不共享 session 对象
    - APScheduler 线程池下可安全调用
    - 数据库异常被吞掉并记录日志，不影响调度链路（与 log_failure 一致）

    降级策略：
    - 若数据库不可用，读取方法返回 None/空列表，写入方法静默失败
    - 此时退化为"内存行为"（重试计数可能因重启归零），但不中断调度
    """

    @staticmethod
    def get(api_code):
        """获取指定 api_code 的重试状态。

        Returns:
            RetryState 对象 or None（不存在或异常）

        注意：返回前 expunge 使对象脱离 session，避免 close 后访问属性报错。
        """
        session = None
        try:
            session = SessionLocal()
            state = session.query(RetryState).filter_by(api_code=api_code).first()
            if state is not None:
                session.expunge(state)
            return state
        except Exception as e:
            logger.error(f"[DB] retry_state get 失败（吞掉）: {e}")
            return None
        finally:
            try:
                if session is not None:
                    session.close()
            except Exception:
                pass

    @staticmethod
    def get_retry_count(api_code):
        """获取重试计数（异常时返回 0，等价于内存字典 .get(api_code, 0)）。"""
        state = RetryStateRepository.get(api_code)
        return state.retry_count if state else 0

    @staticmethod
    def get_last_exc_type(api_code):
        """获取最近异常类型（异常时返回 None，等价于内存字典 .get(api_code)）。"""
        state = RetryStateRepository.get(api_code)
        return state.last_exc_type if state else None

    @staticmethod
    def upsert(api_code, retry_count=None, last_exc_type=None,
               scheduled_next_time=None, job_id=None, func_path=None,
               timeout_sec=None, args_json=None):
        """新增或更新重试状态（只更新非 None 字段）。

        Returns:
            bool: 是否成功
        """
        session = None
        try:
            session = SessionLocal()
            state = session.query(RetryState).filter_by(api_code=api_code).first()
            if state is None:
                state = RetryState(
                    api_code=api_code,
                    retry_count=retry_count or 0,
                    last_exc_type=last_exc_type,
                    last_exc_time=datetime.now() if last_exc_type else None,
                    scheduled_next_time=scheduled_next_time,
                    job_id=job_id,
                    func_path=func_path,
                    timeout_sec=timeout_sec,
                    args_json=args_json,
                    updated_at=datetime.now(),
                )
                session.add(state)
            else:
                if retry_count is not None:
                    state.retry_count = retry_count
                if last_exc_type is not None:
                    state.last_exc_type = last_exc_type
                    state.last_exc_time = datetime.now()
                if scheduled_next_time is not None:
                    state.scheduled_next_time = scheduled_next_time
                if job_id is not None:
                    state.job_id = job_id
                if func_path is not None:
                    state.func_path = func_path
                if timeout_sec is not None:
                    state.timeout_sec = timeout_sec
                if args_json is not None:
                    state.args_json = args_json
                state.updated_at = datetime.now()
            session.commit()
            return True
        except Exception as e:
            try:
                session.rollback()
            except Exception:
                pass
            logger.error(f"[DB] retry_state upsert 失败（吞掉）: {e}")
            return False
        finally:
            try:
                if session is not None:
                    session.close()
            except Exception:
                pass

    @staticmethod
    def delete(api_code):
        """删除重试状态（任务成功时调用）。"""
        session = None
        try:
            session = SessionLocal()
            state = session.query(RetryState).filter_by(api_code=api_code).first()
            if state is not None:
                session.delete(state)
                session.commit()
            return True
        except Exception as e:
            try:
                session.rollback()
            except Exception:
                pass
            logger.error(f"[DB] retry_state delete 失败（吞掉）: {e}")
            return False
        finally:
            try:
                if session is not None:
                    session.close()
            except Exception:
                pass

    @staticmethod
    def list_pending():
        """列出所有有待执行重试任务的状态（scheduled_next_time 不为空）。

        用于调度器启动时恢复未完成的重试任务。

        Returns:
            list[RetryState]: 异常时返回空列表

        注意：返回前 expunge 使对象脱离 session，避免 close 后访问属性报错。
        """
        session = None
        try:
            session = SessionLocal()
            result = session.query(RetryState).filter(
                RetryState.scheduled_next_time.isnot(None)
            ).all()
            for r in result:
                session.expunge(r)
            return result
        except Exception as e:
            logger.error(f"[DB] retry_state list_pending 失败（吞掉）: {e}")
            return []
        finally:
            try:
                if session is not None:
                    session.close()
            except Exception:
                pass

    @staticmethod
    def clear_scheduled(api_code):
        """清除已安排的重试时间（重试任务开始执行时调用，避免重复恢复）。

        注意：不删除整条记录，只清空 scheduled_next_time，
        保留 retry_count/last_exc_type 供后续判定。
        """
        session = None
        try:
            session = SessionLocal()
            state = session.query(RetryState).filter_by(api_code=api_code).first()
            if state is not None:
                state.scheduled_next_time = None
                state.updated_at = datetime.now()
                session.commit()
            return True
        except Exception as e:
            try:
                session.rollback()
            except Exception:
                pass
            logger.error(f"[DB] retry_state clear_scheduled 失败（吞掉）: {e}")
            return False
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
            new_dept_id = contract_data.get('dept_id', existing.dept_id)
            # 'UNKNOWN' 不覆盖已有真实 dept_id
            if new_dept_id == 'UNKNOWN':
                new_dept_id = existing.dept_id
            existing.dept_id = new_dept_id
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
            new_dept_id = contract_data.get('dept_id')
            if new_dept_id == 'UNKNOWN':
                new_dept_id = None
            session.add(ContractBasic(
                contract_id=contract_id,
                dept_id=new_dept_id,
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
        logger.info(f"[DB] upsert合同基础信息: {contract_id} dept={contract_data.get('dept_id')}")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def save_contract_daily_data(contract_id, curve_date_str, electricity_data, price_data, log_callback=None):
    session = SessionLocal()
    try:
        curve_date = datetime.strptime(curve_date_str, '%Y-%m-%d').date()
        
        # 合并 electricity 和 price 为一次 upsert
        merged = {}
        for tp, electricity in electricity_data.items():
            merged[tp] = {
                'contract_id': contract_id,
                'curve_date': curve_date,
                'time_point': tp,
                'electricity': float(electricity),
                'price': None,
            }
        for tp, price in price_data.items():
            if tp in merged:
                merged[tp]['price'] = float(price)
            else:
                merged[tp] = {
                    'contract_id': contract_id,
                    'curve_date': curve_date,
                    'time_point': tp,
                    'electricity': None,
                    'price': float(price),
                }
        rows = list(merged.values())
        _upsert(
            ContractDailyData, session, rows,
            index_elements=['contract_id', 'curve_date', 'time_point'],
            set_fields=['electricity', 'price']
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


def upsert_tenant(dept_id, dept_name, is_active=1):
    """新增或更新租户信息"""
    session = SessionLocal()
    try:
        dept_name = str(dept_name).strip() if dept_name else ''
        if not dept_id:
            raise ValueError("dept_id 不能为空")

        existing = session.query(Tenant).filter_by(dept_id=dept_id).first()
        if existing:
            existing.dept_name = dept_name
            existing.is_active = is_active
            existing.updated_at = datetime.now()
        else:
            session.add(Tenant(
                dept_id=dept_id,
                dept_name=dept_name,
                is_active=is_active,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ))
        session.commit()
        logger.info(f"[DB] upsert租户: {dept_name} (deptId={dept_id})")
    except Exception as e:
        session.rollback()
        logger.error(f"[DB] upsert租户失败: {e}")
        raise e
    finally:
        session.close()


def list_null_metering_records(limit=500):
    """列出 dept_id 为 NULL 的用电数据记录，用于手动回填"""
    session = SessionLocal()
    try:
        records = session.query(MeteringQuery).filter(
            MeteringQuery.dept_id.is_(None)
        ).order_by(MeteringQuery.query_date.desc()).limit(limit).all()

        result = []
        for r in records:
            result.append({
                'id': r.id,
                'query_date': r.query_date.strftime('%Y-%m-%d') if r.query_date else '',
                'cons_no': r.cons_no,
                'mid': r.mid,
                'mname': r.mname or '',
            })
        return result
    except Exception as e:
        logger.error(f"[DB] 查询NULL用电记录失败: {e}")
        raise e
    finally:
        session.close()


def list_null_contract_records(limit=500):
    """列出 dept_id 为 NULL 的合同记录，用于手动回填"""
    session = SessionLocal()
    try:
        records = session.query(ContractBasic).filter(
            ContractBasic.dept_id.is_(None)
        ).order_by(ContractBasic.contract_id.desc()).limit(limit).all()

        result = []
        for r in records:
            result.append({
                'contract_id': r.contract_id,
                'contract_name': r.contract_name or '',
                'buyer': r.buyer or '',
                'seller': r.seller or '',
                'contract_type': r.contract_type or '',
            })
        return result
    except Exception as e:
        logger.error(f"[DB] 查询NULL合同记录失败: {e}")
        raise e
    finally:
        session.close()


def backfill_metering_dept(record_ids, dept_id):
    """批量回填 metering_query 的 dept_id。遇到唯一索引冲突时跳过。"""
    session = SessionLocal()
    try:
        updated = 0
        skipped = 0
        for rid in record_ids:
            record = session.query(MeteringQuery).filter_by(id=rid).first()
            if not record or record.dept_id is not None:
                continue
            # 检查回填后是否会与已有数据冲突（唯一键: dept_id + cons_no + mid + query_date）
            exists = session.query(MeteringQuery).filter(
                MeteringQuery.dept_id == dept_id,
                MeteringQuery.cons_no == record.cons_no,
                MeteringQuery.mid == record.mid,
                MeteringQuery.query_date == record.query_date,
                MeteringQuery.id != record.id,
            ).first()
            if exists:
                skipped += 1
                continue
            record.dept_id = dept_id
            updated += 1
        session.commit()
        if skipped > 0:
            logger.info(f"[DB] 回填用电数据: 成功 {updated} 条，跳过冲突 {skipped} 条")
        else:
            logger.info(f"[DB] 回填用电数据: 成功 {updated} 条")
        return updated
    except Exception as e:
        session.rollback()
        logger.error(f"[DB] 回填用电数据失败: {e}")
        raise e
    finally:
        session.close()


def backfill_contract_dept(contract_ids, dept_id):
    """批量回填 contract_basic 的 dept_id"""
    session = SessionLocal()
    try:
        updated = 0
        for cid in contract_ids:
            record = session.query(ContractBasic).filter_by(contract_id=cid).first()
            if record and record.dept_id is None:
                record.dept_id = dept_id
                updated += 1
        session.commit()
        logger.info(f"[DB] 回填合同数据 dept_id: 共处理 {len(contract_ids)} 条，成功 {updated} 条")
        return updated
    except Exception as e:
        session.rollback()
        logger.error(f"[DB] 回填合同数据失败: {e}")
        raise e
    finally:
        session.close()

