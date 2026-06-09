"""爬虫服务入口 — 独立进程启动 Spider Engine 执行采集任务。

启动参数：
    --template <name>:<params>      指定模板名称和参数（如 google_patent:keyword=LED）
    --param-file <path>            从 JSON 文件读取多个模板参数批量执行
    --list-file <path>             从文本文件逐行读取参数值（每行一个值）
    --list-param <name>           指定用 --list-file 读取的参数名（如 publication_number）
    --delay <n>                    批量执行时的延迟（秒，默认 1.5）
    --start <n>                    批量执行时从第 n 行开始（0-based）
    --limit <n>                    批量执行时最多执行 n 个
    --resume                       从 checkpoint 恢复中断的采集任务

优先级：
    1. 命令行参数（--template, --list-file, --param-file）
    2. 模板中的 batch_params 配置
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Generator, Optional

from app.config.settings import settings
from app.engine.spider_engine import SpiderEngine
from app.engine.template_loader import TemplateLoader
from app.models.template import SiteTemplate

logger = logging.getLogger(__name__)


class BatchParamReader:
    """批量参数文件读取器 - 使用生成器模式支持大文件处理。"""

    def __init__(
        self,
        file_path: str,
        param_name: str,
        start_line: int = 0,
        limit: Optional[int] = None,
    ):
        """
        初始化批量参数读取器。

        Args:
            file_path: 参数文件路径
            param_name: 参数名称
            start_line: 起始行号（0-based）
            limit: 最大读取数量
        """
        self.file_path = Path(file_path)
        self.param_name = param_name
        self.start_line = start_line
        self.limit = limit

    def _validate_file(self) -> None:
        """
        验证文件是否存在且可读。

        Raises:
            FileNotFoundError: 文件不存在
            PermissionError: 文件权限不足
            OSError: 其他文件操作错误
        """
        if not self.file_path.exists():
            raise FileNotFoundError(f"参数文件不存在: {self.file_path}")
        
        if not self.file_path.is_file():
            raise OSError(f"路径不是文件: {self.file_path}")
        
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                f.read(1)
        except PermissionError:
            raise PermissionError(f"没有文件读取权限: {self.file_path}")
        except OSError as e:
            raise OSError(f"读取文件失败: {self.file_path}, 错误: {e}")

    def read(self) -> Generator[tuple[str, int], None, None]:
        """
        使用生成器逐行读取文件内容。

        Yields:
            (参数值, 行号) 元组

        Raises:
            FileNotFoundError: 文件不存在
            PermissionError: 文件权限不足
            OSError: 其他文件操作错误
            UnicodeDecodeError: 文件编码错误
        """
        self._validate_file()
        
        line_count = 0
        yielded_count = 0
        
        logger.info(f"开始读取参数文件: {self.file_path}")
        
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f):
                    line_count += 1
                    
                    # 跳过起始行之前的行
                    if line_num < self.start_line:
                        continue
                    
                    # 检查是否达到限制
                    if self.limit is not None and yielded_count >= self.limit:
                        logger.info(f"已达到限制数量 {self.limit}，停止读取")
                        break
                    
                    # 处理行内容
                    value = line.strip()
                    if value:
                        yielded_count += 1
                        yield value, line_num
        
        except UnicodeDecodeError as e:
            logger.error(f"文件编码错误，请确保文件使用 UTF-8 编码: {e}")
            raise
        except OSError as e:
            logger.error(f"读取文件时发生错误: {e}")
            raise
        
        logger.info(f"文件读取完成，共 {line_count} 行，有效参数 {yielded_count} 个")


def parse_template_arg(arg: str) -> tuple[str, dict[str, str]]:
    """解析模板参数字符串。"""
    parts = arg.split(":")
    name = parts[0]
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.strip()] = value.strip()
    return name, params


def load_param_file(file_path: str) -> list[dict[str, Any]]:
    """从 JSON 文件读取多个模板参数。"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"参数文件不存在: {file_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        return [data]
    else:
        raise ValueError(f"参数文件必须包含一个列表或字典: {file_path}")


async def run_single_template(
    engine: SpiderEngine,
    template: SiteTemplate,
    params: dict[str, str],
) -> bool:
    """
    运行单个模板的采集。

    Args:
        engine: Spider Engine 实例
        template: 模板实例
        params: 参数字典

    Returns:
        是否成功
    """
    try:
        result = await engine.crawl(template)
        if result.success:
            logger.info(f"✓ 成功采集: {template.name}, 参数: {params}")
            return True
        else:
            logger.warning(f"✗ 采集失败: {template.name}, 错误: {result.errors}")
            return False
    except Exception as e:
        logger.exception(f"✗ 采集异常: {template.name}, 错误: {e}")
        return False


async def run_batch_from_config(
    template_name: str,
    template: SiteTemplate,
    batch_config: Any,
) -> None:
    """
    根据模板中的 batch_params 配置运行批量采集。

    Args:
        template_name: 模板名称
        template: 模板实例
        batch_config: 批量配置
    """
    loader = TemplateLoader()
    engine = SpiderEngine()
    
    logger.info("=" * 80)
    logger.info(f"使用模板配置的批量参数: {template_name}")
    logger.info(f"  文件路径: {batch_config.file_path}")
    logger.info(f"  参数名称: {batch_config.param_name}")
    logger.info(f"  起始行: {batch_config.start_line}")
    logger.info(f"  限制数量: {batch_config.limit}")
    logger.info(f"  请求延迟: {batch_config.delay}秒")
    logger.info("=" * 80)
    
    success_count = 0
    fail_count = 0
    skip_count = 0
    
    try:
        reader = BatchParamReader(
            file_path=batch_config.file_path,
            param_name=batch_config.param_name,
            start_line=batch_config.start_line,
            limit=batch_config.limit,
        )
        
        for idx, (value, line_num) in enumerate(reader.read()):
            params = {batch_config.param_name: value}
            
            logger.info(f"[{idx + 1}] 处理第 {line_num} 行: {value}")
            
            try:
                # 重新加载模板并应用参数
                tmpl = loader.load(template_name, param_values=params)
                
                success = await run_single_template(engine, tmpl, params)
                
                if success:
                    success_count += 1
                else:
                    fail_count += 1
            
            except Exception as e:
                logger.exception(f"处理第 {line_num} 行时发生异常: {value}, 错误: {e}")
                fail_count += 1
            
            # 添加延迟
            if batch_config.delay > 0:
                await asyncio.sleep(batch_config.delay)
    
    except FileNotFoundError as e:
        logger.error(f"批量采集失败: {e}")
        return
    except PermissionError as e:
        logger.error(f"批量采集失败: {e}")
        return
    except UnicodeDecodeError as e:
        logger.error(f"批量采集失败: 文件编码错误，请使用 UTF-8 编码")
        return
    except Exception as e:
        logger.exception(f"批量采集发生异常: {e}")
        return
    finally:
        await engine.close()
    
    # 输出统计
    logger.info("=" * 80)
    logger.info(f"批量采集完成！")
    logger.info(f"  成功: {success_count}")
    logger.info(f"  失败: {fail_count}")
    logger.info(f"  跳过: {skip_count}")
    logger.info("=" * 80)


async def run_from_list_file_stream(
    template_name: str,
    file_path: str,
    param_name: str,
    start_line: int = 0,
    limit: Optional[int] = None,
    delay: float = 0,
    batch_size: int = 100,
) -> None:
    """
    使用流式方式从list-file批量读取并采集，每批 batch_size 条。

    Args:
        template_name: 模板名称
        file_path: 参数文件路径
        param_name: 参数名称
        start_line: 起始行号
        limit: 最大处理数量
        delay: 请求延迟
        batch_size: 每批处理数量（默认 100）
    """
    from app.config.settings import settings

    loader = TemplateLoader()
    engine = SpiderEngine()
    success_count = 0
    fail_count = 0
    batch_count = 0
    
    try:
        reader = BatchParamReader(
            file_path=file_path,
            param_name=param_name,
            start_line=start_line,
            limit=limit,
        )
        
        batch: list[str] = []
        batch_start_line = 0
        all_batches = []
        
        # 首先收集所有批次
        for value, line_num in reader.read():
            if not batch:
                batch_start_line = line_num
            batch.append(value)
            
            if len(batch) >= batch_size:
                all_batches.append((batch.copy(), batch_start_line, line_num))
                batch = []
        
        # 处理剩余不足 batch_size 的数据
        if batch:
            all_batches.append((batch.copy(), batch_start_line, line_num))
        
        batch_count = len(all_batches)
        if batch_count == 0:
            logger.info("没有数据需要处理")
            return
        
        logger.info(f"找到 {batch_count} 个批次，启动并发采集，max_concurrent_tasks={settings.max_concurrent_tasks}")
        
        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
        tasks = []
        
        async def _worker(batch_data: list[str], start_line_num: int, end_line_num: int, idx: int) -> int:
            """单个任务的 worker 函数。"""
            async with semaphore:
                joined = _build_batch_patent_param(batch_data)
                params = {param_name: joined}
                logger.info(
                    f"[批次 {idx + 1}/{batch_count}] 行 {start_line_num}-{end_line_num}, "
                    f"共 {len(batch_data)} 条: {batch_data[0]}...{batch_data[-1]}"
                )
                
                try:
                    tmpl = loader.load(template_name, param_values=params)
                    result = await engine.crawl(tmpl)
                    
                    if result.success:
                        logger.info(f"✓ 批次 {idx + 1} 成功")
                        return len(batch_data)
                    else:
                        logger.warning(f"✗ 批次 {idx + 1} 失败: {result.errors}")
                        return -len(batch_data)
                
                except Exception as e:
                    logger.exception(f"✗ 批次 {idx + 1} 异常: {e}")
                    return -len(batch_data)
        
        # 创建所有任务
        for idx, (batch_data, start_line_num, end_line_num) in enumerate(all_batches):
            task = _worker(batch_data, start_line_num, end_line_num, idx)
            tasks.append(task)
        
        # 使用 asyncio.gather 并发执行所有任务
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 统计结果
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"批次异常: {result}")
            elif result > 0:
                success_count += result
            else:
                fail_count += abs(result)
        
        logger.info("=" * 80)
        logger.info(
            f"流式采集完成！共 {batch_count} 批, "
            f"成功: {success_count}, 失败: {fail_count}"
        )
        logger.info("=" * 80)
    
    except Exception as e:
        logger.exception(f"流式采集发生异常: {e}")
    
    finally:
        await engine.close()


def _build_batch_patent_param(ids: list[str]) -> str:
    """构建批量专利查询参数值。

    格式: (ID1)+OR+ID2+OR+ID3。 ID 用 +OR+ 拼接。"""
    return "+OR+".join(ids)


async def run_from_command_line(
    template_args: list[tuple[str, dict[str, str]]],
    delay: float = 0,
) -> None:
    """
    根据命令行参数运行采集（用于非list-file的批量参数）。

    Args:
        template_args: 模板参数列表 [(name, params), ...]
        delay: 请求延迟
    """
    from app.config.settings import settings

    loader = TemplateLoader()

    # 加载所有模板
    templates = []
    for name, params in template_args:
        try:
            tmpl = loader.load(name, param_values=params or None)
            templates.append((tmpl, params))
        except FileNotFoundError as e:
            logger.error(str(e))
        except ValueError as e:
            logger.error(f"参数错误: {e}")

    if not templates:
        logger.error("没有找到有效的模板")
        return

    logger.info(f"找到 {len(templates)} 个有效模板")
    logger.info(f"启动并发采集，max_concurrent_tasks={settings.max_concurrent_tasks}")

    # 创建单个 engine 实例，所有任务共享
    engine = SpiderEngine()

    # 创建信号量控制并发
    semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
    success_count = 0
    fail_count = 0
    tasks = []

    async def _worker(template: SiteTemplate, params: dict[str, str], idx: int, total: int) -> bool:
        """单个任务的 worker 函数。"""
        async with semaphore:
            logger.info("=" * 80)
            logger.info(f"采集 [{idx + 1}/{total}]: {template.name}")
            if params:
                logger.info(f"  参数: {params}")

            try:
                result = await engine.crawl(template)
                success = result.success

                if success:
                    logger.info(f"✓ 成功: {template.name}, 参数: {params}")
                    return True
                else:
                    logger.warning(f"✗ 失败: {template.name}, 错误: {result.errors}")
                    return False

            except Exception as e:
                logger.exception(f"✗ 异常: {template.name}, 错误: {e}")
                return False

    try:
        # 创建所有任务
        for idx, (template, params) in enumerate(templates):
            task = _worker(template, params, idx, len(templates))
            tasks.append(task)

        # 使用 asyncio.gather 并发执行所有任务
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 统计结果
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"任务异常: {result}")
                fail_count += 1
            elif result:
                success_count += 1
            else:
                fail_count += 1

        logger.info("=" * 80)
        logger.info(f"采集完成！成功: {success_count}, 失败: {fail_count}")
        logger.info("=" * 80)

    finally:
        await engine.close()


def setup_logging(service: str = "crawler") -> None:
    """设置日志。"""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{service.upper()}] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    
def log_infra_status() -> None:
    logging.info("=" * 60)
    logging.info("Infrastructure Status")
    logging.info("=" * 60)
    logging.info("  Storage:    %s", "MongoDB" if settings.db_url else "File (local)")
    logging.info("  File Store: %s", "MinIO" if settings.minio_endpoint else "Local filesystem")
    logging.info("  Messaging:  %s", "Kafka" if settings.kafka_brokers else "Disabled")
    logging.info("  Templates:  %s", settings.template_dir)
    logging.info("  Output:     %s", settings.output_dir)
    logging.info("  Concurrency: %d", settings.max_concurrent_tasks)
    logging.info("-" * 60)
    logging.info("Enhanced Modules:")
    logging.info("  Anti-Crawl:   %s", "ON" if settings.anti_crawl_enabled else "OFF (default)")
    logging.info("  Dedup/Bloom:  %s", "ON" if settings.dedup_enabled else "OFF (default)")
    logging.info("  Scheduler:    %s", "ON" if settings.scheduler_enabled else "OFF (default)")
    logging.info("  Jinja2 Tpl:   %s", "ON" if settings.jinja2_enabled else "OFF (default)")
    logging.info("=" * 60)

def main() -> None:
    """主函数。"""
    parser = argparse.ArgumentParser(description="Spider Crawler Service")
    
    # 基本模板参数
    parser.add_argument(
        "--template", "-t",
        action="append",
        dest="template_args",
        help="Template with params, e.g., google_patent:assignee=Google",
    )
    
    # JSON 参数文件
    parser.add_argument(
        "--param-file", "-p",
        help="JSON file with multiple template params",
    )
    
    # 文本列表文件
    parser.add_argument(
        "--list-file", "-f",
        help="Text file with one param value per line",
    )
    parser.add_argument(
        "--list-param",
        help="Param name for --list-file values (e.g., publication_number)",
    )
    parser.add_argument(
        "--template-name",
        default="google_patent_by_id",
        help="Template name for --list-file mode (default: google_patent_by_id)",
    )
    
    # 批量处理选项
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Start from line N (0-based) in list-file",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of items to process",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Delay between requests in seconds (default: 1.5)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for list-file mode (default: 100)",
    )
    
    args = parser.parse_args()
    
    setup_logging("crawler")
    log_infra_status()
    
    # 优先处理 --list-file
    if args.list_file:
        if not args.list_param:
            logger.error("错误: 使用 --list-file 时必须同时指定 --list-param")
            sys.exit(1)
        
        asyncio.run(run_from_list_file_stream(
            template_name=args.template_name,
            file_path=args.list_file,
            param_name=args.list_param,
            start_line=args.start,
            limit=args.limit,
            delay=args.delay,
            batch_size=args.batch_size,
        ))
    else:
        # 处理其他参数（--template, --param-file）
        template_args: list[tuple[str, dict[str, str]]] = []
        
        # 处理 --template 参数
        if args.template_args:
            for arg in args.template_args:
                name, params = parse_template_arg(arg)
                template_args.append((name, params))
        
        # 处理 --param-file 参数
        if args.param_file:
            param_list = load_param_file(args.param_file)
            for item in param_list:
                name = item.get("template", args.template_name)
                params = item.get("params", {})
                template_args.append((name, params))
        
        # 运行
        if template_args:
            log_infra_status()
            asyncio.run(run_from_command_line(template_args, delay=args.delay))

if __name__ == "__main__":
    main()
