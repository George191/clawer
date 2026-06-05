"""生成爬虫参数文件 - 用于批量抓取专利"""

import json
from pathlib import Path
from typing import List, Dict, Any

from app.tools.load_local_patents import LoadLocalPatents


def generate_param_file(
    pub_nums_file: str,
    output_file: str,
    limit: int = None,
    start_from: int = 0
) -> List[Dict[str, Any]]:
    """
    生成爬虫参数JSON文件
    
    Args:
        pub_nums_file: 专利公开编号文件
        output_file: 输出的参数文件
        limit: 限制数量
        start_from: 从第几个开始
    
    Returns:
        参数列表
    """
    # 加载专利公开编号
    loader = LoadLocalPatents(data_dir="d:/code/spider/data")
    all_pub_nums = loader.load_from_txt(pub_nums_file)
    
    # 切片
    target_pub_nums = all_pub_nums[start_from:start_from+limit] if limit else all_pub_nums[start_from:]
    
    # 生成参数列表
    params_list = []
    for pub_num in target_pub_nums:
        params_list.append({
            "template": "google_patent_by_id",
            "params": {
                "publication_number": pub_num
            }
        })
    
    # 保存到文件
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(params_list, f, ensure_ascii=False, indent=2)
    
    print(f"✓ 已生成参数文件: {output_file}")
    print(f"  包含 {len(params_list)} 个专利")
    
    return params_list


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="生成爬虫参数文件")
    parser.add_argument(
        "--input",
        type=str,
        default="d:/code/spider/data/publication_numbers.txt",
        help="专利公开编号文件"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="d:/code/spider/data/scrape_params.json",
        help="输出的参数文件"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制数量"
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="起始位置"
    )
    
    args = parser.parse_args()
    
    generate_param_file(
        pub_nums_file=args.input,
        output_file=args.output,
        limit=args.limit,
        start_from=args.start
    )
