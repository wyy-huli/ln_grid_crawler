# 数据库读写接口
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from .models import Base, ApiConfig, FetchBatch, TimeSeriesData, UnitStatus, MeteringQuery, FetchFailureLog, ContractBasic, ContractDailyData
from utils.config import DATABASE_URL
from datetime import datetime, date
import json

engine = create_engine(DATABASE_URL, pool_pre_ping=True,pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)

def get_or_create_api(api_code, api_name, fetch_type='type1'):
    session = SessionLocal()
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
    api_id = api.api_id
    session.close()
    return api_id

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
        print(f"[DB] 保存 {api_name} {len(records)} 条")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

def upsert_type2_data(api_code, api_name, target_date_str, records):
    """类型2：逐点更新，每天只保留一个批次，每次抓取覆盖对应时间点"""
    if not records:
        print(f"[DB] {api_name} 无数据，跳过存储")
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
        for r in records:
            tp = r['x'][:5]
            value = float(r['y'])
            # SQLite upsert: INSERT OR REPLACE
            stmt = sqlite_insert(TimeSeriesData).values(
                batch_id=batch.batch_id,
                time_point=tp,
                value=value
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=['batch_id', 'time_point'],
                set_={'value': value}
            )
            session.execute(stmt)
            updated_points.append(tp)

        session.commit()
        print(f"[DB] upsert {api_name} {len(records)} 条，时间点: {updated_points[:3]}...")
        count = session.execute(
            text("SELECT COUNT(*) FROM time_series_data WHERE batch_id = :bid"),
            {"bid": batch.batch_id}
        ).scalar()
        print(f"[DB] 批次 {batch.batch_id} 当前总点数: {count}")
    except Exception as e:
        session.rollback()
        print(f"[DB] upsert失败: {e}")
        raise e
    finally:
        session.close()

def save_type4_data(records):
    """records: list of dict from objectList"""
    if not records: return
    session = SessionLocal()
    try:
        for item in records:
            guid = item.get('guid')
            exists = session.query(UnitStatus).filter_by(guid=guid).first()
            if not exists:
                session.add(UnitStatus(
                    business_time=item.get('businessTime'),
                    unit_name=item.get('name'),
                    unit_number=item.get('number'),
                    capacity=item.get('volume'),
                    status=item.get('item'),
                    cause=item.get('cause'),
                    apply_id=item.get('applyId'),
                    guid=guid
                ))
        session.commit()
        print(f"[DB] 保存机组状态 {len(records)} 条")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

def save_type3_query(query_date, cons_no, mid, response_json):
    session = SessionLocal()
    session.add(MeteringQuery(query_date=query_date, cons_no=cons_no, mid=mid, response_json=response_json))
    session.commit()
    session.close()

def log_failure(api_code, reason):
    session = SessionLocal()
    session.add(FetchFailureLog(api_code=api_code, target_time=datetime.now(), reason=reason))
    session.commit()
    session.close()


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
        print(f"[DB] 保存合同基础信息: {contract_id}")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def save_contract_daily_data(contract_id, curve_date_str, electricity_data, price_data, log_callback=None):
    session = SessionLocal()
    try:
        curve_date = datetime.strptime(curve_date_str, '%Y-%m-%d').date()
        
        for tp, electricity in electricity_data.items():
            stmt = sqlite_insert(ContractDailyData).values(
                contract_id=contract_id,
                curve_date=curve_date,
                time_point=tp,
                electricity=float(electricity),
                price=None
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=['contract_id', 'curve_date', 'time_point'],
                set_={'electricity': float(electricity)}
            )
            session.execute(stmt)
        
        for tp, price in price_data.items():
            stmt = sqlite_insert(ContractDailyData).values(
                contract_id=contract_id,
                curve_date=curve_date,
                time_point=tp,
                electricity=None,
                price=float(price)
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=['contract_id', 'curve_date', 'time_point'],
                set_={'price': float(price)}
            )
            session.execute(stmt)
        
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
        
        print(f"[DB] 保存合同日数据: {contract_id} {curve_date_str}")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

