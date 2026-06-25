from pymongo import MongoClient, DeleteOne

def delete_duplicates():
    client = MongoClient("mongodb://mongoadmin:mongoadmin@10.10.0.200:27017/")
    db = client["raw_data"]
    collection = db["ssc_press"]
    
    target_field = "_meta.record_id"  # 您的业务去重字段
    
    # 聚合管道：找出重复数据
    pipeline = [
        {"$group": {
            "_id": f"${target_field}",
            "count": {"$sum": 1},
            "docs": {"$push": "$_id"}  # 收集所有的系统 _id
        }},
        {"$match": {"count": {"$gt": 1}}}
    ]
    
    print("正在分析重复数据...")
    cursor = collection.aggregate(pipeline, allowDiskUse=True)
    
    bulk_ops = []
    total_deleted = 0
    BATCH_SIZE = 1000  # 每 1000 条执行一次批量删除
    
    for item in cursor:
        doc_ids = item["docs"]
        
        # 决定保留哪一条：
        # 保留最早插入的一条：doc_ids[1:] （因为默认按插入顺序，第一条最早）
        # 保留最新插入的一条：doc_ids[:-1]
        ids_to_delete = doc_ids[1:] 
        
        for _id in ids_to_delete:
            bulk_ops.append(DeleteOne({"_id": _id}))
            
        # 达到批次大小，执行删除
        if len(bulk_ops) >= BATCH_SIZE:
            result = collection.bulk_write(bulk_ops, ordered=False)
            total_deleted += result.deleted_count
            print(f"已成功清理 {total_deleted} 条重复文档...")
            bulk_ops = []
            
    # 处理剩余的尾数数据
    if bulk_ops:
        result = collection.bulk_write(bulk_ops, ordered=False)
        total_deleted += result.deleted_count
        
    print(f"🎉 清理完毕！共安全删除了 {total_deleted} 条重复数据。")

if __name__ == "__main__":
    # 执行前强烈建议先备份数据库！
    delete_duplicates()
