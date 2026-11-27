# MinIO 分布式存储池部署

本目录准备目标集群的基础架构配置，沿用当前 MinIO 版本和原生 systemd 部署方式。
目前尚未创建目标虚拟机或数据盘，也未迁移数据、修改现有服务或切换访问入口。
可以先准备虚拟机系统盘、主机名和配置，数据盘后续添加；缺少数据盘时目标服务必须保持停止。

## 已核实的源端

2026-11-24 的只读巡检结果：

- 服务地址：`192.168.20.61:9000` 和 `192.168.1.40:9000`，均位于同一台虚拟机。
- 版本：`RELEASE.2025-04-22T22-12-26Z`；本次迁移不同时升级 MinIO。
- 启动目录：`/sdb/minio`，由现有 `minio.service` 提供服务。
- `/dev/sdb1` 为直接挂载的 XFS，约 60 TiB，未使用 LVM。
- `/dev/sdc1` 挂载在 `/srv/terrain`，已有业务用途，不作为目标空盘。
- 桶：`internal`、`metadata`、`train.level1.1mbps`；`metadata` 有匿名读取策略，其余两桶没有该策略。
- 三个桶均未启用对象版本控制、对象锁、生命周期、默认加密、桶复制或通知；IAM 中另有 4 个用户。
- 管理接口统计约 6,949 万个对象、10.43 TB 对象逻辑数据；XFS 实际占用约 24.1 TiB。容量规划不能只看对象逻辑大小。

巡检日志位于工作区 `output/minio_distributed_migration_20261124/`，不包含登录密码。
现有单盘格式不能通过给启动参数追加数据目录直接变成目标分布式格式。

## 初始迁移资源预检

2026-11-24 19:17（Asia/Shanghai）仅按授权只读检查了 ESXi `192.168.20.81`，未登录另外两台宿主机。

| 资源 | 实际状态 |
| --- | --- |
| `datastore1` 容量 | 87.19 TiB |
| datastore 实际空闲 | 4.82 TiB |
| 已配置但尚未实际占用的存储量（uncommitted） | 25.33 TiB，不能视为可用空间 |
| 物理内存 | 127.51 GiB |
| 当前已用内存 | 111.71 GiB，余量约 15.81 GiB，未扣目标虚拟机开销 |
| 目标 MinIO 节点、空数据盘 | 尚未准备 |

当前资源不足以让源端与目标集群并存，初始对象复制尚未启动。
先提供足以承接全量数据、纠删码、元数据及增长空间的目标存储，并安排目标节点内存，之后才能部署和迁移。
源端 XFS 内约 35.9 TiB 空闲属于已分配给原虚拟机的数据盘，不等于 ESXi 可以直接分配给新虚拟机的空闲存储。
不能用扩大薄置备盘的标称容量代替真实可用空间，也不清空源盘来腾迁移空间。

当前 `datastore1` 已超过 [Broadcom 文档所述 VMFS5/6 单 datastore 64 TB 支持上限](https://knowledge.broadcom.com/external/article/410951/vmfs-datastore-maximum-supported-capacit.html)。
新目标存储应另行规划受支持的 datastore 布局，不在本次迁移准备中重建承载其他现有虚拟机的 datastore。
完整资源记录位于 `output/minio_distributed_migration_20261124/esxi-inventory.json`。

## 目标布局

首个池采用 4 个虚拟节点，每个节点配置 1 块独立挂载的数据 VMDK，使用 `EC:2`。
四块数据盘必须同容量，理论对象数据容量约为原始容量的一半，另需预留元数据和增长空间。
实际磁盘容量、虚拟机资源、IP 和 ESXi datastore 要在资源盘点后确定，本目录不预留未经核实的 IP 或容量。

| 节点名 | 数据盘挂载 | MinIO 数据目录 | API / Console |
| --- | --- | --- | --- |
| `minio-p1n1` | `/mnt/minio/data1` | `/mnt/minio/data1/minio` | `9000` / `9001` |
| `minio-p1n2` | `/mnt/minio/data1` | `/mnt/minio/data1/minio` | `9000` / `9001` |
| `minio-p1n3` | `/mnt/minio/data1` | `/mnt/minio/data1/minio` | `9000` / `9001` |
| `minio-p1n4` | `/mnt/minio/data1` | `/mnt/minio/data1/minio` | `9000` / `9001` |

这些虚拟机可部署在现有 ESXi 上，无需先新增物理服务器。
同一 ESXi 上的四台虚拟机不提供宿主机故障容错；各 VMDK 的物理存储归属也必须单独记录。
不能将同一文件系统中的四个目录当成四块独立数据盘。

## 新节点就绪条件

以下条件满足前，不启动目标服务，不复制生产对象：

1. 目标节点、IP、CPU、内存和真实可分配存储已经确定；源端保持可用，并有足够容量同时保存迁移中的源和目标数据。
2. 四个节点的主机名在所有节点上解析一致，节点间 API 端口可达，时间同步正常。
3. 安装相同的 `RELEASE.2025-04-22T22-12-26Z` 原生 MinIO，二进制来源及 SHA-256 验证完成。
4. 新数据盘已按设备标识核实为空盘，创建 XFS，并固定挂载到 `/mnt/minio/data1`。
5. 创建系统用户及组 `minio-user`；数据目录由该用户拥有，权限为 `0700`。
6. 从现有服务安全迁移 root 凭证；实际凭证只保存在节点上受保护的配置中。

每个节点的 `/etc/fstab` 使用该节点实际数据盘的 UUID，例如：

```text
UUID=<该节点数据盘的实际UUID> /mnt/minio/data1 xfs defaults,noatime 0 2
```

不要给该数据盘添加 `nofail`。挂载失败必须明确报错，不能让 MinIO 将数据写入系统盘上的同名目录。
`minio-pool.service` 同时检查实际挂载点、数据目录可写性和必填凭证，缺盘或缺配置会拒绝启动。
已核实挂载成功后，才创建 `/mnt/minio/data1/minio`；不要在旧数据盘上执行初始化。

## 配置安装

仅在目标节点通过 Git 获取本目录配置后执行，不覆盖源端现有 `minio.service` 或 `/etc/minio/minio.conf`。
先将 `minio-pool.conf.example` 中的 root 凭证填入节点上的受保护配置，并确认节点名解析正确。
不要把填写后的配置或凭证提交到 Git。

```bash
sudo install -d -m 0755 /etc/minio
sudo install -m 0600 infra/minio/minio-pool.conf.example /etc/minio/minio-pool.conf
sudoedit /etc/minio/minio-pool.conf
sudo install -m 0644 infra/minio/minio-pool.service /etc/systemd/system/minio-pool.service
sudo systemd-analyze verify /etc/systemd/system/minio-pool.service
```

资源和配置通过检查后，在四个目标节点上协调执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now minio-pool.service
```

通过目标端的 `mc ready` 和 `mc admin info` 验证四节点、四数据盘、一个池及 `EC:2`。
主机名尚未分配或数据盘尚未添加时，不执行这一步。

## 后续增加存储池

准备第二组四个节点和数据盘，命名为 `minio-p2n1` 至 `minio-p2n4`。
保持原池完整，在所有八个节点上使用相同的端点列表：

```text
MINIO_VOLUMES="http://minio-p1n{1...4}:9000/mnt/minio/data1/minio http://minio-p2n{1...4}:9000/mnt/minio/data1/minio"
```

新池必须满足现有 `EC:2` 的盘数及布局要求。当前版本需要协调重启所有节点，不能按单节点滚动方式操作。
验证新池在线后，MinIO 自动按各池可用空间分配后续写入；已有对象不会自动重新均衡。
增加物理硬盘、创建 VMDK、初始化新盘、挂载及注册新池仍需明确操作，不做未知磁盘的自动格式化或自动接管。

## 生产数据迁移与切换

目标集群就绪后，迁移另行执行：

1. 先迁移三个桶的访问策略及必要配置，保留 IAM 用户和应用访问凭证，核对 `metadata` 的匿名读取行为。
2. 保留所有桶名、对象键、数据库引用和任务绑定，包括正在使用的 `google_patent/v1.0` 与 `v1.1`。不增加模板版本或备份对象。
3. 使用 **6 个独立 OS 工作进程**、**每批最多 3,000 个对象**；任务范围互斥，持久化进度和错误日志，重启时续接已提交检查点。迁移逻辑保存在独立脚本中。
4. 初次复制期间源端继续工作。切换前暂停所有源端写入方，等待在途上传完成，再完成最终差异同步；同时核对删除和同键覆盖。
5. 全量核对对象键与大小、元数据、失败清单及范围覆盖；模板和适配器验证内容哈希，并检查资源下载及预签名访问。
6. 保留原内部和前端访问入口。代理或入口切换方案在目标 IP 和现有端口使用情况明确后确定。
7. 验证新入口后恢复写入。旧源保留到验收结束；新集群出现新增写入后，回滚必须先处理这些数据，不能只切回旧地址。

当前没有目标节点或空数据盘，不启动对象复制，也不更改源端格式。

## 参考

- [当前版本的分布式部署及按池扩容说明](https://github.com/minio/minio/blob/RELEASE.2025-04-22T22-12-26Z/docs/distributed/README.md)
- [MinIO 关于不能直接向现有节点追加磁盘的说明](https://github.com/minio/minio/discussions/20291)
