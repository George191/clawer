"""批量专利抓取脚本 - 使用 app.crawler.main"""

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def load_publication_numbers(file_path: str) -> list[str]:
    """从文件加载专利公开编号"""
    pub_nums = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pub_nums.append(line)
    return pub_nums


def scrape_one_patent(pub_num: str) -> bool:
    """使用 app.crawler.main 抓取单个专利"""
    try:
        template_arg = f"google_patent_by_id:publication_number={pub_num}"
        
        result = subprocess.run(
            [sys.executable, "-m", "app.crawler.main", template_arg],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # 检查是否成功
        success = result.returncode == 0
        
        if success:
            logger.info(f"✓ 成功抓取: {pub_num}")
        else:
            logger.warning(f"✗ 抓取失败: {pub_num}")
            logger.debug(f"  stderr: {result.stderr}")
        
        return success
        
    except subprocess.TimeoutExpired:
        logger.error(f"✗ 抓取超时: {pub_num}")
        return False
    except Exception as e:
        logger.exception(f"✗ 抓取异常: {pub_num}")
        return False


def main(limit: int = 10, start_from: int = 0, delay: float = 1.5):
    """主函数"""
    pub_nums_file = Path("d:/code/spider/data/publication_numbers.txt")
    
    # 加载专利公开编号
    pub_nums = load_publication_numbers(str(pub_nums_file))
    
    # 切片
    target_pub_nums = pub_nums[start_from:start_from+limit] if limit else pub_nums[start_from:]
    
    logger.info(f"开始批量抓取")
    logger.info(f"  总数: {len(target_pub_nums)}")
    logger.info(f"  延迟: {delay}秒")
    
    success_count = 0
    fail_count = 0
    
    for i, pub_num in enumerate(target_pub_nums):
        logger.info("=" * 60)
        logger.info(f"进度: {i+1}/{len(target_pub_nums)}")
        
        success = scrape_one_patent(pub_num)
        
        if success:
            success_count += 1
        else:
            fail_count += 1
        
        # 延迟
        if delay > 0 and i < len(target_pub_nums) - 1:
            time.sleep(delay)
    
    # 总结
    logger.info("=" * 60)
    logger.info("抓取完成！")
    logger.info(f"  成功: {success_count}")
    logger.info(f"  失败: {fail_count}")
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="批量专利抓取")
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
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="请求延迟（秒）"
    )
    
    args = parser.parse_args()
    
    main(
        limit=args.limit,
        start_from=args.start,
        delay=args.delay
    )
