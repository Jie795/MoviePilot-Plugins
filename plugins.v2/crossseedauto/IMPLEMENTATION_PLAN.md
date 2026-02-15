# 跨站自动辅种插件 (CrossSeedAuto) - 实现计划

## 插件概述

基于"文件名 + 媒体元数据 + 文件大小容差"的跨站匹配算法，实现非 Infohash 依赖的自动化辅种功能。

## 作者信息
- 插件名称: 跨站自动辅种
- 插件描述: 基于文件名和媒体元数据的智能跨站辅种，支持主辅分离和自动止损
- 插件版本: 1.0
- 插件作者: [从WxPusherMsg获取]
- 作者主页: [从WxPusherMsg获取]

---

## 实现步骤

### 第一阶段：基础框架搭建 ✓

**目标**: 创建插件基础结构和配置界面

**任务清单**:
- [x] 创建插件目录结构 `plugins.v2/crossseedauto/`
- [x] 实现插件主类 `CrossSeedAuto`，继承 `_PluginBase`
- [x] 定义插件元数据（名称、描述、版本、作者等）
- [x] 实现 `get_form()` 方法，创建配置表单
- [x] 实现 `init_plugin()` 方法，加载配置
- [x] 实现 `get_state()` 方法，返回插件状态

**配置项设计**:
```python
{
    "enabled": bool,              # 启用插件
    "cron": str,                  # 执行周期（cron表达式）
    "onlyonce": bool,             # 立即运行一次
    "notify": bool,               # 发送通知
    "downloader": str,            # 下载器名称
    "target_sites": list,         # 目标辅种站点列表
    "exclude_tags": list,         # 排除标签（黑名单）
    "size_tolerance": float,      # 文件大小容差（MB，默认0.01）
    "enable_split_mode": bool,    # 启用主辅分离模式
    "search_cooldown": int,       # 搜索冷却时间（秒，默认5-10随机）
    "max_retry": int,             # 最大重试次数（默认3）
}
```

**输出文件**:
- `plugins.v2/crossseedauto/__init__.py` (基础框架)

---

### 第二阶段：种子扫描与过滤模块

**目标**: 实现种子扫描、黑名单过滤、站点排重

**任务清单**:
- [ ] 集成 `DownloaderHelper` 获取下载器实例
- [ ] 实现 `_scan_torrents()` 方法：扫描已完成种子
- [ ] 实现 `_filter_torrents()` 方法：
  - 过滤带有排除标签的种子
  - 过滤已在缓存中的种子（成功/失败）
  - 识别种子所属站点并排重
- [ ] 实现缓存管理：
  - `_load_cache()`: 加载成功/失败缓存
  - `_save_cache()`: 保存缓存
  - `_clear_cache()`: 清理缓存（手动触发）

**数据结构**:
```python
# 缓存结构
{
    "success": {
        "种子hash": {
            "source_site": "站点ID",
            "target_sites": ["站点ID1", "站点ID2"],
            "timestamp": "2026-02-15 10:00:00"
        }
    },
    "failed": {
        "种子hash": {
            "source_site": "站点ID",
            "reason": "失败原因",
            "retry_count": 3,
            "timestamp": "2026-02-15 10:00:00"
        }
    }
}
```

**输出文件**:
- 更新 `plugins.v2/crossseedauto/__init__.py`

---

### 第三阶段：元数据提取模块

**目标**: 从种子路径提取媒体信息

**任务清单**:
- [ ] 实现 `_extract_metadata()` 方法：
  - 调用飞牛 API 获取媒体库信息
  - 解析返回的电影名、剧集名称、年份
- [ ] 实现 `_parse_torrent_name()` 方法（兜底策略）：
  - 使用正则表达式解析种子名称
  - 提取关键词：标题、年份、分辨率、编码等
- [ ] 实现 `_normalize_title()` 方法：
  - 标准化标题格式（去除特殊字符、统一大小写）

**正则表达式示例**:
```python
# 提取标题和年份
r"^(.+?)[\.\s]+(\d{4})[\.\s]+"
# 提取分辨率
r"(720p|1080p|2160p|4K)"
# 提取编码
r"(H\.264|H\.265|x264|x265|HEVC)"
```

**输出文件**:
- 更新 `plugins.v2/crossseedauto/__init__.py`
- 创建 `plugins.v2/crossseedauto/metadata.py` (可选，如果逻辑复杂)

---

### 第四阶段：跨站检索与校验模块

**目标**: 在目标站点搜索匹配种子并校验文件大小

**任务清单**:
- [ ] 集成 `SitesHelper` 获取站点信息
- [ ] 实现 `_search_sites()` 方法：
  - 遍历目标站点
  - 使用 RSS 或站点 API 搜索
  - 添加随机冷却时间（5-10秒）
- [ ] 实现 `_validate_file_size()` 方法：
  - 比较源种子和目标种子的文件大小
  - 容差范围：0.01MB（可配置）
- [ ] 实现 `_match_files()` 方法：
  - 比较文件列表（文件名、大小）
  - 返回匹配度评分

**搜索策略**:
```python
# 搜索关键词构建
keywords = f"{title} {year} {resolution}"
# 站点搜索（使用站点API或RSS）
results = site.search(keywords)
# 文件大小校验
for result in results:
    if abs(result.size - source_size) <= tolerance:
        # 匹配成功
        matched_torrents.append(result)
```

**输出文件**:
- 更新 `plugins.v2/crossseedauto/__init__.py`
- 创建 `plugins.v2/crossseedauto/matcher.py` (可选)

---

### 第五阶段：推送与主辅分离模块

**目标**: 将匹配的种子添加到下载器，支持主辅分离

**任务清单**:
- [ ] 实现 `_add_torrent()` 方法：
  - 获取下载器实例
  - 根据配置选择推送模式（主辅分离/默认）
- [ ] 实现主辅分离逻辑：
  - 设置保存路径为原文件路径
  - 禁止移动和重命名
- [ ] 实现状态监听 `_monitor_torrent_status()` 方法：
  - 添加种子后立即检测状态
  - 如果状态为"下载中"，判定为辅种失败
  - 执行自动止损：删除种子和源文件

**推送逻辑**:
```python
if enable_split_mode:
    # 主辅分离模式
    save_path = original_path
    options = {
        "autoTMM": False,  # 禁用自动种子管理
        "paused": False
    }
else:
    # 默认模式
    save_path = default_path
    options = {}

downloader.add_torrent(torrent_url, save_path, options)
```

**输出文件**:
- 更新 `plugins.v2/crossseedauto/__init__.py`

---

### 第六阶段：缓存与通知模块

**目标**: 管理辅种缓存，发送通知消息

**任务清单**:
- [ ] 实现成功缓存管理：
  - 记录辅种成功的种子hash、源站点、目标站点
- [ ] 实现失败缓存管理：
  - 记录辅种失败的种子hash、失败原因、重试次数
- [ ] 实现缓存清理功能：
  - 手动清理缓存（通过配置开关）
  - 自动清理过期缓存（可选）
- [ ] 实现通知功能：
  - 辅种成功通知
  - 辅种失败通知
  - 批量辅种完成通知

**通知消息格式**:
```
【跨站自动辅种】
种子名称: {torrent_name}
源站点: {source_site}
目标站点: {target_sites}
辅种结果: 成功/失败
失败原因: {reason}（如果失败）
```

**输出文件**:
- 更新 `plugins.v2/crossseedauto/__init__.py`

---

### 第七阶段：定时任务与服务注册

**目标**: 注册定时任务，实现自动化辅种

**任务清单**:
- [ ] 实现 `get_service()` 方法：
  - 根据 cron 表达式注册定时任务
  - 支持立即运行一次
- [ ] 实现主任务 `_cross_seed_task()` 方法：
  - 扫描种子
  - 过滤种子
  - 提取元数据
  - 跨站检索
  - 推送种子
  - 更新缓存
  - 发送通知
- [ ] 实现 `stop_service()` 方法：
  - 停止定时任务

**定时任务逻辑**:
```python
def get_service(self):
    if self._enabled and self._cron:
        return [{
            "id": "CrossSeedAuto",
            "name": "跨站自动辅种服务",
            "trigger": CronTrigger.from_crontab(self._cron),
            "func": self._cross_seed_task,
            "kwargs": {}
        }]
```

**输出文件**:
- 更新 `plugins.v2/crossseedauto/__init__.py`

---

### 第八阶段：异常处理与优化

**目标**: 完善异常处理，优化性能

**任务清单**:
- [ ] 实现频率限制：
  - 站点搜索添加随机冷却时间（5-10秒）
  - 防止被站点封IP
- [ ] 实现重试机制：
  - 搜索失败自动重试（最多3次）
  - 推送失败自动重试
- [ ] 实现日志记录：
  - 记录关键操作日志
  - 记录错误日志
- [ ] 实现性能优化：
  - 使用线程池并发搜索多个站点
  - 缓存站点信息，减少重复请求

**异常处理示例**:
```python
try:
    results = self._search_sites(keywords)
except Exception as e:
    logger.error(f"搜索站点失败: {str(e)}")
    self._update_failed_cache(torrent_hash, reason=str(e))
    return
```

**输出文件**:
- 更新 `plugins.v2/crossseedauto/__init__.py`

---

### 第九阶段：插件详情页面

**目标**: 实现插件详情页面，展示辅种历史

**任务清单**:
- [ ] 实现 `get_page()` 方法：
  - 展示最近辅种记录
  - 展示成功/失败统计
  - 展示缓存信息
- [ ] 设计页面布局：
  - 使用卡片展示辅种记录
  - 使用图表展示统计数据（可选）

**页面数据结构**:
```python
{
    "history": [
        {
            "torrent_name": "种子名称",
            "source_site": "源站点",
            "target_sites": ["目标站点1", "目标站点2"],
            "status": "成功/失败",
            "timestamp": "2026-02-15 10:00:00"
        }
    ]
}
```

**输出文件**:
- 更新 `plugins.v2/crossseedauto/__init__.py`

---

### 第十阶段：测试与文档

**目标**: 测试插件功能，编写使用文档

**任务清单**:
- [ ] 单元测试：
  - 测试种子扫描
  - 测试元数据提取
  - 测试文件大小校验
  - 测试缓存管理
- [ ] 集成测试：
  - 测试完整辅种流程
  - 测试主辅分离模式
  - 测试自动止损
- [ ] 编写使用文档：
  - 插件功能说明
  - 配置项说明
  - 常见问题解答
- [ ] 编写 README.md

**输出文件**:
- `plugins.v2/crossseedauto/README.md`
- `plugins.v2/crossseedauto/tests/` (可选)

---

## 技术栈

- **Python 3.8+**
- **MoviePilot V2 插件框架**
- **DownloaderHelper**: 下载器管理
- **SitesHelper**: 站点管理
- **APScheduler**: 定时任务
- **RequestUtils**: HTTP 请求

---

## 依赖项

```txt
# requirements.txt (如果需要额外依赖)
# 目前使用 MoviePilot 内置依赖，无需额外安装
```

---

## 注意事项

1. **站点流量保护**: 搜索接口必须添加随机冷却时间（5-10秒），防止被封IP
2. **缓存管理**: 成功/失败缓存防止循环重试，节省站点流量
3. **自动止损**: 添加种子后立即检测状态，如果状态为"下载中"，立即删除种子和源文件
4. **主辅分离**: 新站点种子保存路径指向原文件路径，但不允许移动或重命名
5. **文件大小容差**: 默认0.01MB，可配置
6. **重试机制**: 最多重试3次，防止无限循环

---

## 开发进度

- [x] 第一阶段：基础框架搭建 ✅ (已完成)
  - ✅ 创建插件目录和主类
  - ✅ 实现配置表单和初始化逻辑
  - ✅ 添加插件定义到 package.v2.json
  - ✅ 实现基础服务注册和停止逻辑
- [x] 第二阶段：种子扫描与过滤模块 ✅ (已完成)
  - ✅ 集成 DownloaderHelper 获取下载器实例
  - ✅ 实现 _scan_torrents() 扫描已完成种子
  - ✅ 实现 _filter_torrents() 过滤逻辑
  - ✅ 实现缓存管理（加载、保存、清理）
  - ✅ 实现站点识别和排重
  - ✅ 添加清理缓存配置选项
- [ ] 第三阶段：元数据提取模块
- [ ] 第四阶段：跨站检索与校验模块
- [ ] 第五阶段：推送与主辅分离模块
- [ ] 第六阶段：缓存与通知模块
- [ ] 第七阶段：定时任务与服务注册
- [ ] 第八阶段：异常处理与优化
- [ ] 第九阶段：插件详情页面
- [ ] 第十阶段：测试与文档

---

## 预计完成时间

- 总计：10个阶段
- 预计工作量：每阶段1-2小时
- 总计：10-20小时

---

## 联系方式

如有问题，请联系插件作者。
