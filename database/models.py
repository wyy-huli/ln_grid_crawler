# SQLAlchemy 模型
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, BigInteger, ForeignKey, Text, SmallInteger, UniqueConstraint, Index
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class ApiConfig(Base):
    __tablename__ = 'api_config'
    api_id = Column(Integer, primary_key=True, autoincrement=True)
    api_code = Column(String(50), unique=True, nullable=False)
    api_name = Column(String(100), nullable=False)
    fetch_type = Column(String(10), default='type1')
    fetch_freq = Column(String(20), default='1d')
    is_active = Column(SmallInteger, default=1)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class FetchBatch(Base):
    __tablename__ = 'fetch_batch'
    batch_id = Column(Integer, primary_key=True, autoincrement=True)
    api_id = Column(Integer, ForeignKey('api_config.api_id'), nullable=False)
    target_date = Column(Date, nullable=False)
    fetch_time = Column(DateTime, default=datetime.now)
    is_latest = Column(SmallInteger, default=1)

class TimeSeriesData(Base):
    __tablename__ = 'time_series_data'
    data_id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey('fetch_batch.batch_id', ondelete='CASCADE'), nullable=False)
    time_point = Column(String(5), nullable=False)
    value = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint('batch_id', 'time_point', name='uq_batch_timepoint'),
        Index('idx_batch_id', 'batch_id'),
    )

class UnitStatus(Base):
    __tablename__ = 'unit_status'
    id = Column(Integer, primary_key=True, autoincrement=True)
    business_time = Column(String(8), nullable=False)
    unit_name = Column(String(100), nullable=False)
    unit_number = Column(String(50))
    capacity = Column(String(20))
    status = Column(String(20))
    cause = Column(String(100))
    apply_id = Column(String(64))
    guid = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_unit_status_biztime', 'business_time'),
        Index('idx_unit_status_unitname', 'unit_name'),
    )

class Tenant(Base):
    __tablename__ = 'tenant'
    dept_id = Column(String(64), primary_key=True)
    dept_name = Column(String(200), nullable=False)
    is_active = Column(SmallInteger, default=1)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class MeteringQuery(Base):
    __tablename__ = 'metering_query'
    id = Column(Integer, primary_key=True, autoincrement=True)
    dept_id = Column(String(64))
    query_date = Column(Date, nullable=False)
    cons_no = Column(String(50), nullable=False)
    mid = Column(String(64), nullable=False)
    mname = Column(String(100))
    response_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('dept_id', 'cons_no', 'mid', 'query_date', name='uq_metering_dept_cons_mid_date'),
    )

class FetchFailureLog(Base):
    __tablename__ = 'fetch_failure_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    api_code = Column(String(50), nullable=False)
    target_time = Column(DateTime, nullable=False)
    reason = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)


class RetryState(Base):
    """重试状态持久化表（Q0-3 修复：替代内存字典 _retry_counts / _last_exc_type_map）。

    用途：
    - 调度器重启后能恢复未完成的重试任务
    - 避免重试计数因重启归零导致重复重试
    - 记录最近异常类型，用于编程错误判定（重启后仍生效）
    """
    __tablename__ = 'retry_state'
    api_code = Column(String(50), primary_key=True)
    retry_count = Column(SmallInteger, default=0, nullable=False)
    last_exc_type = Column(String(100))  # 最近一次异常类型（编程错误判定用）
    last_exc_time = Column(DateTime)
    scheduled_next_time = Column(DateTime)  # 已安排的重试执行时间
    job_id = Column(String(100))  # 对应的 APScheduler job_id
    func_path = Column(String(200))  # 重试任务函数路径（重启恢复用）
    timeout_sec = Column(Integer)  # 子进程超时秒数（重启恢复用）
    args_json = Column(Text)  # 位置参数 JSON 序列化（重启恢复用，可为空）
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ContractBasic(Base):
    __tablename__ = 'contract_basic'
    contract_id = Column(String(64), primary_key=True)
    dept_id = Column(String(64))
    contract_name = Column(String(200), nullable=False)
    seller = Column(String(200))
    buyer = Column(String(200))
    contract_type = Column(String(100))
    contract_sequence = Column(String(100))
    contract_electricity = Column(Float)
    monthly_electricity = Column(Float)
    monthly_price = Column(Float)
    curve_status = Column(String(50))
    settlement_point = Column(String(200))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index('idx_contract_basic_buyer', 'buyer'),
        Index('idx_contract_basic_type', 'contract_type'),
        Index('idx_contract_basic_dept', 'dept_id'),
    )


class ContractDailyData(Base):
    __tablename__ = 'contract_daily_data'
    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(String(64), ForeignKey('contract_basic.contract_id', ondelete='CASCADE'), nullable=False)
    curve_date = Column(Date, nullable=False)
    time_point = Column(String(5), nullable=False)
    electricity = Column(Float)
    price = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('contract_id', 'curve_date', 'time_point', name='uq_contract_date_timepoint'),
        Index('idx_contract_daily_contract', 'contract_id'),
        Index('idx_contract_daily_date', 'curve_date'),
    )