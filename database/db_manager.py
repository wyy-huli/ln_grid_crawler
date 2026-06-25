# 数据库读写接口
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from .models import Base, ApiConfig, FetchBatch, TimeSeriesData, UnitStatus, MeteringQuery, FetchFailureLog
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

