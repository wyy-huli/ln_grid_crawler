# 日志
# utils/logger.py
import logging
import os
from logging.handlers import RotatingFileHandler
from utils.config import DATA_DIR

LOG_DIR = os.path.join(DATA_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, 'crawler.log')

# 日志格式
formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 根 logger
logger = logging.getLogger('grid_crawler')
logger.setLevel(logging.DEBUG)

# 控制台 handler（INFO 级别）
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# 文件 handler（DEBUG 级别，保留最近 5MB，最多 3 个备份）
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

# 添加 handler
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# 简化的快捷函数
def info(msg):
    logger.info(msg)

def debug(msg):
    logger.debug(msg)

def warning(msg):
    logger.warning(msg)

def error(msg):
    logger.error(msg)

def exception(msg):
    logger.exception(msg)