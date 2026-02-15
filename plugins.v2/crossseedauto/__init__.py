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
            self._size_tolerance = config.get("size_tolerance", 0.01)
            self._enable_split_mode = config.get("enable_split_mode", False)
            self._search_cooldown_min = config.get("search_cooldown_min", 5)
            self._search_cooldown_max = config.get("search_cooldown_max", 10)
            self._max_retry = config.get("max_retry", 3)

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
        })

    def _cross_seed_task(self):
        """
        跨站辅种主任务
        """
        logger.info("开始执行跨站自动辅种任务")
        try:
            # TODO: 实现辅种逻辑
            logger.info("跨站自动辅种任务执行完成")
        except Exception as e:
            logger.error(f"跨站自动辅种任务执行失败: {str(e)}")

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
        # 获取下载器选项
        downloader_options = []
        if self._downloader_helper:
            downloader_options = [
                {"title": config.name, "value": config.name}
                for config in self._downloader_helper.get_configs().values()
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
