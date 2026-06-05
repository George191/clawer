"""ETL Pipeline — 通用专利数据抽取、转换、加载管道。

三层架构：
- RDS (Raw Data Store):  接收 Kafka 原始数据，解析入库 Postgres
- ODS (Operational Data Store): 标准化字段、异常处理
- ADS (Application Data Store): 算法分析、结果入库

消息标注：每条消息携带 _pipeline_meta 标注数据源和数据类型
"""