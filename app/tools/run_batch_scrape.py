"""使用 app.crawler.main 进行批量专利抓取"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import List, Dict, Any

from app.crawler.main import run
from app.tools.load_local_patents import LoadLocalPatents
from app.tools.generate_scrape_params import generate_param_file

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def run_batch(
    pub_nums_file: str,
    limit: int = None,
    start_from: int = 0
) -> None:
    """
    运行批量抓取
    
    Args:
        pub_nums_file: 专利公开编号文件
        limit: 限制数量
        start_from: 起始位置
    """
    # 生成临时参数文件
    param_file = Path("d:/code/spider/data/temp_scrape_params.json")
    
    logger.info("生成参数文件...")
    params_list = generate_param_file(
        pub_nums_file=pub_nums_file,
        output_file=str(param_file),
        limit=limit,
        start_from=start_from
    )
    
    if not params_list:
        logger.warning("没有专利需要抓取")
        return
    
    # 逐个使用 app.crawler.main 抓取
    logger.info(f"开始抓取 {len(params_list)} 个专利...")
    
    for i, item in enumerate(params_list):
        pub_num = item["params"]["publication_number"]
        template_arg = f"{item['template']}:publication_number={pub_num}"
        
        logger.info(f"\n{'=' * 60}")
        logger.info(f"进度: {i+1}/{len(params_list)}")
        logger.info(f"专利: {pub_num}")
        logger.info('=' * 60)
        
        try:
            await run([template_arg])
        except Exception as e:
            logger.exception(f"抓取失败 {pub_num}: {e}")
        
        # 延迟避免请求过快
        if i < len(params_list) - 1:
            await asyncio.sleep(1.5)
    
    logger.info("\n" + "=" * 60)
    logger.info("批量抓取完成！")
    logger.info("=" * 60)
    
    # 清理临时文件
    try:
        param_file.unlink()
    except:
        pass


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="使用 app.crawler.main 批量抓取专利")
    parser.add_argument(
        "--file",
        type=str,
        default="d:/code/spider/data/publication_numbers.txt",
        help="专利公开编号文件"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="限制抓取数量"
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="起始位置"
    )
    
    args = parser.parse_args()
    
    asyncio.run(run_batch(
        pub_nums_file=args.file,
        limit=args.limit,
        start_from=args.start
    ))
