import random
import time
from datetime import datetime, timedelta
from typing import Any, List, Dict, Tuple, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.db.site_oper import SiteOper
from app.helper.downloader import DownloaderHelper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType


class CrossSeedAuto(_PluginBase):
    # 插件名称
    plugin_name = "跨站自动辅种"
    # 插件描述
    plugin_desc = "基于文件名和媒体元数据的智能跨站辅种，支持主辅分离和自动止损。"
    # 插件图标
    plugin_icon = "crossseed.png"
    # 插件版本
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "zhjay"
    # 作者主页
    author_url = "https://github.com/Jie795"
    # 插件配置项ID前缀
    plugin_config_prefix = "crossseedauto_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled: bool = False
    _cron: str = ""
    _onlyonce: bool = False
    _notify: bool = False
    _downloader: str = ""
    _target_sites: list = []
    _exclude_tags: list = []
    _size_tolerance: float = 0.01
    _enable_split_mode: bool = False
    _search_cooldown_min: int = 5
    _search_cooldown_max: int = 10
    _max_retry: int = 3
    _clear_cache: bool = False

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    # 辅助类
    _downloader_helper: Optional[DownloaderHelper] = None
    _sites_helper: Optional[SitesHelper] = None

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        # 停止现有任务
        self.stop_service()

        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "")
            self._onlyonce = config.get("onlyonce", False)
            self._notify = config.get("notify", False)
            self._downloader = config.get("downloader", "")
            self._target_sites = config.get("target_sites", [])
            self._exclude_tags = config.get("exclude_tags", [])
            if isinstance(self._exclude_tags, str):
                self._exclude_tags = [tag.strip() for tag in self._exclude_tags.split(',') if tag.strip()]
            self._size_tolerance = config.get("size_tolerance", 0.01)
            self._enable_split_mode = config.get("enable_split_mode", False)
            self._search_cooldown_min = config.get("search_cooldown_min", 5)
            self._search_cooldown_max = config.get("search_cooldown_max", 10)
            self._max_retry = config.get("max_retry", 3)
            self._clear_cache = config.get("clear_cache", False)
            
            # 清理缓存
            if self._clear_cache:
                self._clear_cache()
                self._clear_cache = False
                self.__update_config()

        # 初始化辅助类
        if self._enabled or self._onlyonce:
            self._downloader_helper = DownloaderHelper()
            self._sites_helper = SitesHelper()

            # 立即运行一次
            if self._onlyonce:
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                logger.info("跨站自动辅种服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self._cross_seed_task,
                    trigger='date',
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="跨站自动辅种"
                )

                # 关闭一次性开关
                self._onlyonce = False
                self.__update_config()

                # 启动任务
                if self._scheduler.get_jobs():
                    self._scheduler.print_jobs()
                    self._scheduler.start()

    def get_state(self) -> bool:
        """
        获取插件状态
        """
        return self._enabled

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "notify": self._notify,
            "downloader": self._downloader,
            "target_sites": self._target_sites,
            "exclude_tags": self._exclude_tags,
            "size_tolerance": self._size_tolerance,
            "enable_split_mode": self._enable_split_mode,
            "search_cooldown_min": self._search_cooldown_min,
            "search_cooldown_max": self._search_cooldown_max,
            "max_retry": self._max_retry,
            "clear_cache": False,  # 清理后重置
        })

    def _cross_seed_task(self):
        """
        跨站辅种主任务
        """
        logger.info("开始执行跨站自动辅种任务")
        try:
            # 加载缓存
            cache = self._load_cache()
            
            # 扫描种子
            torrents = self._scan_torrents()
            if not torrents:
                logger.info("未扫描到已完成的种子")
                return
            
            logger.info(f"扫描到 {len(torrents)} 个已完成种子")
            
            # 过滤种子
            filtered_torrents = self._filter_torrents(torrents, cache)
            if not filtered_torrents:
                logger.info("过滤后无需辅种的种子")
                return
            
            logger.info(f"过滤后需要辅种的种子数量: {len(filtered_torrents)}")
            
            # TODO: 后续阶段实现
            # - 提取元数据
            # - 跨站检索
            # - 推送种子
            # - 更新缓存
            # - 发送通知
            
            logger.info("跨站自动辅种任务执行完成")
        except Exception as e:
            logger.error(f"跨站自动辅种任务执行失败: {str(e)}")

    def _scan_torrents(self) -> List[Dict[str, Any]]:
        """
        扫描下载器中已完成的种子
        """
        if not self._downloader:
            logger.error("未配置下载器")
            return []
        
        try:
            # 获取下载器实例
            downloader_service = self._downloader_helper.get_service(name=self._downloader)
            if not downloader_service:
                logger.error(f"未找到下载器: {self._downloader}")
                return []
            
            downloader = downloader_service.instance
            if not downloader:
                logger.error(f"下载器实例获取失败: {self._downloader}")
                return []
            
            # 获取所有种子
            torrents = downloader.get_torrents()
            if not torrents:
                return []
            
            # 过滤已完成的种子（做种中或已完成）
            completed_torrents = []
            for torrent in torrents:
                # 检查种子状态
                state = getattr(torrent, 'state', '').lower()
                if state in ['seeding', 'uploading', 'stalledup', 'completed']:
                    completed_torrents.append({
                        'hash': torrent.hash,
                        'name': torrent.name,
                        'size': torrent.size,
                        'save_path': getattr(torrent, 'save_path', ''),
                        'tags': getattr(torrent, 'tags', []),
                        'category': getattr(torrent, 'category', ''),
                        'tracker': self._get_tracker_domain(torrent)
                    })
            
            return completed_torrents
        except Exception as e:
            logger.error(f"扫描种子失败: {str(e)}")
            return []

    def _get_tracker_domain(self, torrent) -> str:
        """
        获取种子的tracker域名
        """
        try:
            trackers = getattr(torrent, 'trackers', [])
            if trackers:
                # 获取第一个有效的tracker
                for tracker in trackers:
                    url = getattr(tracker, 'url', '')
                    if url and '://' in url:
                        # 提取域名
                        domain = url.split('://')[1].split('/')[0]
                        return domain
        except Exception as e:
            logger.debug(f"获取tracker域名失败: {str(e)}")
        return ""

    def _filter_torrents(self, torrents: List[Dict[str, Any]], cache: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        过滤种子
        - 过滤带有排除标签的种子
        - 过滤已在缓存中的种子
        - 识别种子所属站点并排重
        """
        filtered = []
        success_cache = cache.get('success', {})
        failed_cache = cache.get('failed', {})
        
        for torrent in torrents:
            torrent_hash = torrent.get('hash')
            torrent_name = torrent.get('name')
            
            # 检查排除标签
            if self._exclude_tags:
                tags = torrent.get('tags', [])
                if isinstance(tags, str):
                    tags = [tag.strip() for tag in tags.split(',')]
                
                # 检查是否包含排除标签
                has_exclude_tag = False
                for exclude_tag in self._exclude_tags:
                    if exclude_tag in tags:
                        logger.debug(f"种子 {torrent_name} 包含排除标签 {exclude_tag}，跳过")
                        has_exclude_tag = True
                        break
                
                if has_exclude_tag:
                    continue
            
            # 检查成功缓存
            if torrent_hash in success_cache:
                logger.debug(f"种子 {torrent_name} 已在成功缓存中，跳过")
                continue
            
            # 检查失败缓存
            if torrent_hash in failed_cache:
                failed_info = failed_cache[torrent_hash]
                retry_count = failed_info.get('retry_count', 0)
                if retry_count >= self._max_retry:
                    logger.debug(f"种子 {torrent_name} 已达到最大重试次数，跳过")
                    continue
            
            # 识别种子所属站点
            tracker_domain = torrent.get('tracker', '')
            source_site = self._identify_site(tracker_domain)
            if source_site:
                torrent['source_site'] = source_site
                logger.debug(f"种子 {torrent_name} 来自站点: {source_site}")
            
            # 检查是否在目标站点列表中（避免重复辅种到同一站点）
            if source_site and source_site in self._target_sites:
                logger.debug(f"种子 {torrent_name} 的源站点在目标站点列表中，移除该目标站点")
                # 创建副本并移除源站点
                torrent['filtered_target_sites'] = [
                    site for site in self._target_sites if site != source_site
                ]
            else:
                torrent['filtered_target_sites'] = self._target_sites.copy()
            
            if not torrent['filtered_target_sites']:
                logger.debug(f"种子 {torrent_name} 无可用的目标站点，跳过")
                continue
            
            filtered.append(torrent)
        
        return filtered

    def _identify_site(self, tracker_domain: str) -> Optional[str]:
        """
        根据tracker域名识别站点ID
        """
        if not tracker_domain:
            return None
        
        try:
            # 获取所有站点
            sites = SiteOper().list_order_by_pri()
            for site in sites:
                # 检查站点域名是否匹配
                if site.domain and site.domain in tracker_domain:
                    return site.id
        except Exception as e:
            logger.debug(f"识别站点失败: {str(e)}")
        
        return None

    def _load_cache(self) -> Dict[str, Any]:
        """
        加载缓存
        """
        cache = self.get_data('cache')
        if not cache:
            cache = {
                'success': {},
                'failed': {}
            }
        return cache

    def _save_cache(self, cache: Dict[str, Any]):
        """
        保存缓存
        """
        self.save_data('cache', cache)

    def _update_success_cache(self, torrent_hash: str, source_site: str, target_sites: List[str]):
        """
        更新成功缓存
        """
        cache = self._load_cache()
        cache['success'][torrent_hash] = {
            'source_site': source_site,
            'target_sites': target_sites,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        self._save_cache(cache)

    def _update_failed_cache(self, torrent_hash: str, source_site: str, reason: str):
        """
        更新失败缓存
        """
        cache = self._load_cache()
        if torrent_hash in cache['failed']:
            cache['failed'][torrent_hash]['retry_count'] += 1
            cache['failed'][torrent_hash]['last_reason'] = reason
            cache['failed'][torrent_hash]['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        else:
            cache['failed'][torrent_hash] = {
                'source_site': source_site,
                'reason': reason,
                'retry_count': 1,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        self._save_cache(cache)

    def _clear_cache(self):
        """
        清理缓存
        """
        self.save_data('cache', {
            'success': {},
            'failed': {}
        })
        logger.info("缓存已清理")

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        if self._enabled and self._cron:
            return [{
                "id": "CrossSeedAuto",
                "name": "跨站自动辅种服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self._cross_seed_task,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
        """
        # 获取下载器选项（临时初始化以获取配置）
        downloader_helper = DownloaderHelper()
        downloader_options = [
            {"title": config.name, "value": config.name}
            for config in downloader_helper.get_configs().values()
        ]

        # 获取站点选项
        site_options = [
            {"title": site.name, "value": site.id}
            for site in SiteOper().list_order_by_pri()
        ]

        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'enabled',
                                        'label': '启用插件',
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'notify',
                                        'label': '发送通知',
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'onlyonce',
                                        'label': '立即运行一次',
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'enable_split_mode',
                                        'label': '主辅分离模式',
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'clear_cache',
                                        'label': '清理缓存',
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VCronField',
                                    'props': {
                                        'model': 'cron',
                                        'label': '执行周期',
                                        'placeholder': '5位cron表达式'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSelect',
                                    'props': {
                                        'model': 'downloader',
                                        'label': '下载器',
                                        'items': downloader_options
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VSelect',
                                    'props': {
                                        'chips': True,
                                        'multiple': True,
                                        'model': 'target_sites',
                                        'label': '目标辅种站点',
                                        'items': site_options
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'exclude_tags',
                                        'label': '排除标签',
                                        'placeholder': '多个标签用英文逗号分隔'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'size_tolerance',
                                        'label': '文件大小容差(MB)',
                                        'placeholder': '默认0.01'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'search_cooldown_min',
                                        'label': '搜索冷却最小值(秒)',
                                        'placeholder': '默认5'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'search_cooldown_max',
                                        'label': '搜索冷却最大值(秒)',
                                        'placeholder': '默认10'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'max_retry',
                                        'label': '最大重试次数',
                                        'placeholder': '默认3'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [{
                            'component': 'VCol',
                            'props': {'cols': 12},
                            'content': [{
                                'component': 'VAlert',
                                'props': {
                                    'type': 'info',
                                    'variant': 'tonal',
                                    'text': '基于"文件名 + 媒体元数据 + 文件大小容差"的跨站匹配算法，'
                                            '实现非 Infohash 依赖的自动化辅种。支持主辅分离模式和自动止损机制。'
                                }
                            }]
                        }]
                    },
                    {
                        'component': 'VRow',
                        'content': [{
                            'component': 'VCol',
                            'props': {'cols': 12},
                            'content': [{
                                'component': 'VAlert',
                                'props': {
                                    'type': 'warning',
                                    'variant': 'tonal',
                                    'text': '主辅分离模式：新站点种子保存路径指向原文件路径，但不允许移动或重命名。'
                                            '添加种子后立即检测状态，若状态为下载中，判定为辅种失败并自动删除。'
                                }
                            }]
                        }]
                    }
                ]
            }
        ], {
            "enabled": False,
            "cron": "0 2 * * *",
            "onlyonce": False,
            "notify": True,
            "downloader": "",
            "target_sites": [],
            "exclude_tags": [],
            "size_tolerance": 0.01,
            "enable_split_mode": False,
            "search_cooldown_min": 5,
            "search_cooldown_max": 10,
            "max_retry": 3,
            "clear_cache": False,
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面
        """
        # TODO: 实现详情页面
        return [{
            'component': 'VAlert',
            'props': {
                'type': 'info',
                'text': '暂无辅种数据',
                'variant': 'tonal',
            }
        }]

    def stop_service(self):
        """
        停止插件服务
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止插件服务失败: {str(e)}")
