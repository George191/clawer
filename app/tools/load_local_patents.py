import os
from pathlib import Path
from typing import Generator, List, Tuple


class LoadLocalPatents:
    """本地专利数据加载器 - 支持大文件分批读取"""

    def __init__(self, data_dir: str, chunksize: int = 10000):
        """
        初始化加载器
        
        Args:
            data_dir: 数据目录路径
            chunksize: 每批次读取的行数（大文件推荐10000-50000）
        """
        self.data_dir = Path(data_dir)
        self.chunksize = chunksize
        
        # 验证目录存在
        if not self.data_dir.exists():
            raise FileNotFoundError(f"数据目录不存在: {self.data_dir}")

    def get_file_list(self) -> List[Path]:
        """
        获取倒序排列的CSV文件列表
        
        Returns:
            按年份倒序的CSV文件路径列表（2024 -> 2023 -> 2022 -> ...）
        """
        # 获取所有CSV文件
        csv_files = sorted(
            self.data_dir.glob("*.csv"),
            key=lambda x: int(x.stem),  # 按年份排序
            reverse=True  # 倒序：先2024，再2023...
        )
        return csv_files

    def read_publication_numbers(self) -> Generator[Tuple[str, int], None, None]:
        """
        使用chunksize和生成器方式读取所有专利公开编号和年份
        
        Yields:
            (专利公开编号, 年份) 元组
        """
        import pandas as pd
        
        csv_files = self.get_file_list()
        
        for file_path in csv_files:
            print(f"正在读取文件: {file_path.name}")
            
            # 使用chunksize逐块读取，只读取需要的列
            for chunk in pd.read_csv(
                file_path,
                chunksize=self.chunksize,
                usecols=["专利公开编号", "year"],  # 读取专利公开编号和年份
                encoding="utf-8"
            ):
                # 逐个yield专利公开编号和年份
                for _, row in chunk.iterrows():
                    yield row["专利公开编号"], int(row["year"])

    def get_all_publication_numbers(self) -> List[Tuple[str, int]]:
        """
        获取所有专利公开编号和年份列表
        
        Returns:
            (专利公开编号, 年份) 元组的列表
        """
        return list(self.read_publication_numbers())

    def save_publication_numbers(self, output_path: str):
        """
        将所有专利公开编号保存到文件
        
        Args:
            output_path: 输出文件路径
        """
        with open(output_path, "w", encoding="utf-8") as f:
            count = 0
            for pub_num, year in self.read_publication_numbers():
                f.write(f"{pub_num}\n")
                count += 1
                if count % 10000 == 0:
                    print(f"已处理 {count} 条记录...")
        print(f"完成！共保存 {count} 条专利公开编号到 {output_path}")

    def load_from_txt(self, txt_path: str) -> List[str]:
        """
        从文本文件加载专利公开编号
        
        Args:
            txt_path: 文本文件路径，每行一个专利公开编号
            
        Returns:
            专利公开编号列表
        """
        pub_nums = []
        with open(txt_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    pub_nums.append(line)
        return pub_nums


if __name__ == "__main__":
    # 使用示例
    loader = LoadLocalPatents(
        data_dir="d:/code/spider/data",
        chunksize=10000
    )
    
    # 方式1: 使用生成器逐个处理（推荐大文件）
    print("=== 使用生成器读取专利公开编号 ===")
    count = 0
    for pub_num, year in loader.read_publication_numbers():
        print(f"{pub_num} ({year})")
        count += 1
        if count >= 10:  # 只打印前10个作为示例
            print(f"... 还有更多，已打印前10个")
            break
    
    # 方式2: 保存到文件
    print("\n=== 保存专利公开编号到文件 ===")
    loader.save_publication_numbers("d:/code/spider/data/publication_numbers_20_24.txt")
