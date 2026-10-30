"""抽取基础组件；专项任务负责业务字段选择和结果封装。"""

from app.etl.tasks.extractors.base import BaseExtractor
from app.etl.tasks.extractors.keyword import KeywordExtractor

__all__ = ["BaseExtractor", "KeywordExtractor"]
