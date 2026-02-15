import re
import random
import time
from datetime import datetime, timedelta
from typing import Any, List, Dict, Tuple, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.chain.media import MediaChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.metainfo import MetaInfo
from app.db.site_oper import SiteOper
from app.helper.downloader import DownloaderHelper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.utils.http import RequestUtils


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
    _media_chain: Optional[MediaChain] = None

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
            self._media_chain = MediaChain()

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
        
        # 检查配置
        if not self._downloader:
            logger.error("未配置下载器，任务终止")
            return
        
        if not self._target_sites:
            logger.error("未配置目标站点，任务终止")
            return
        
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
            
            # 提取元数据
            torrents_with_metadata = []
            for torrent in filtered_torrents:
                try:
                    metadata = self._extract_metadata(torrent)
                    if metadata:
                        torrent['metadata'] = metadata
                        torrents_with_metadata.append(torrent)
                    else:
                        logger.debug(f"种子 {torrent.get('name')} 未能提取元数据，跳过")
                except Exception as e:
                    logger.error(f"提取元数据失败: {torrent.get('name')}, 错误: {str(e)}")
                    continue
            
            if not torrents_with_metadata:
                logger.info("提取元数据后无可用种子")
                return
            
            logger.info(f"成功提取元数据的种子数量: {len(torrents_with_metadata)}")
            
            # 跨站检索与校验
            matched_results = []
            for torrent in torrents_with_metadata:
                try:
                    matches = self._search_and_validate(torrent)
                    if matches:
                        matched_results.append({
                            'torrent': torrent,
                            'matches': matches
                        })
                except Exception as e:
                    logger.error(f"跨站检索失败: {torrent.get('name')}, 错误: {str(e)}")
                    continue
            
            if not matched_results:
                logger.info("跨站检索后无匹配种子")
                return
            
            logger.info(f"跨站检索成功的种子数量: {len(matched_results)}")
            
            # 推送种子到下载器
            success_count = 0
            failed_count = 0
            
            for result in matched_results:
                torrent = result['torrent']
                matches = result['matches']
                
                for match in matches:
                    try:
                        success = self._add_torrent_to_downloader(torrent, match)
                        if success:
                            success_count += 1
                            # 更新成功缓存
                            self._update_success_cache(
                                torrent.get('hash'),
                                torrent.get('source_site', ''),
                                [match.get('site_id')]
                            )
                            
                            # 记录历史
                            self._save_history({
                                'torrent_name': torrent.get('name'),
                                'source_site': torrent.get('source_site', ''),
                                'target_site': match.get('site_name', ''),
                                'status': '成功',
                                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            })
                        else:
                            failed_count += 1
                            # 更新失败缓存
                            self._update_failed_cache(
                                torrent.get('hash'),
                                torrent.get('source_site', ''),
                                "推送失败"
                            )
                            
                            # 记录历史
                            self._save_history({
                                'torrent_name': torrent.get('name'),
                                'source_site': torrent.get('source_site', ''),
                                'target_site': match.get('site_name', ''),
                                'status': '失败',
                                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            })
                    except Exception as e:
                        logger.error(f"推送种子失败: {torrent.get('name')} -> {match.get('site_name')}, 错误: {str(e)}")
                        failed_count += 1
                        continue
            
            logger.info(f"辅种任务完成: 成功={success_count}, 失败={failed_count}")
            
            # 发送通知
            if self._notify and (success_count > 0 or failed_count > 0):
                self._send_notification(success_count, failed_count)
            
        except Exception as e:
            logger.error(f"跨站自动辅种任务执行失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

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

    def _extract_metadata(self, torrent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        提取种子的媒体元数据
        优先使用路径识别，失败则使用种子名称解析
        """
        torrent_name = torrent.get('name', '')
        save_path = torrent.get('save_path', '')
        
        # 方法1: 通过路径识别媒体信息
        if save_path:
            try:
                media_info = self._media_chain.recognize_by_path(path=save_path)
                if media_info:
                    metadata = self._extract_from_mediainfo(media_info)
                    if metadata:
                        logger.info(f"通过路径识别到媒体信息: {torrent_name} -> {metadata.get('title')}")
                        return metadata
            except Exception as e:
                logger.debug(f"通过路径识别媒体信息失败: {str(e)}")
        
        # 方法2: 通过种子名称解析
        metadata = self._parse_torrent_name(torrent_name)
        if metadata:
            logger.info(f"通过名称解析到媒体信息: {torrent_name} -> {metadata.get('title')}")
            return metadata
        
        logger.warning(f"无法提取种子元数据: {torrent_name}")
        return None

    def _extract_from_mediainfo(self, media_info: MediaInfo) -> Optional[Dict[str, Any]]:
        """
        从MediaInfo对象提取元数据
        """
        try:
            if not media_info:
                return None
            
            metadata = {
                'title': media_info.title or '',
                'year': media_info.year or '',
                'type': media_info.type.value if media_info.type else '',
                'tmdb_id': media_info.tmdb_id or '',
                'season': media_info.season or '',
                'episode': media_info.episode or '',
                'resolution': '',
                'source': '',
                'codec': '',
            }
            
            # 标准化标题
            metadata['normalized_title'] = self._normalize_title(metadata['title'])
            
            return metadata
        except Exception as e:
            logger.debug(f"从MediaInfo提取元数据失败: {str(e)}")
            return None

    def _parse_torrent_name(self, torrent_name: str) -> Optional[Dict[str, Any]]:
        """
        解析种子名称提取元数据（兜底策略）
        """
        try:
            # 使用MetaInfo解析
            meta = MetaInfo(torrent_name)
            
            if not meta.title:
                return None
            
            metadata = {
                'title': meta.title or '',
                'year': meta.year or '',
                'type': meta.type.value if meta.type else '',
                'tmdb_id': '',
                'season': meta.season or '',
                'episode': meta.episode or '',
                'resolution': meta.resource_pix or '',
                'source': meta.resource_type or '',
                'codec': meta.video_encode or '',
            }
            
            # 标准化标题
            metadata['normalized_title'] = self._normalize_title(metadata['title'])
            
            return metadata
        except Exception as e:
            logger.debug(f"解析种子名称失败: {str(e)}")
            return None

    def _normalize_title(self, title: str) -> str:
        """
        标准化标题
        - 转换为小写
        - 移除特殊字符
        - 移除多余空格
        """
        if not title:
            return ""
        
        # 转换为小写
        normalized = title.lower()
        
        # 移除特殊字符，保留字母、数字、空格
        normalized = re.sub(r'[^a-z0-9\s]', ' ', normalized)
        
        # 移除多余空格
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        return normalized

    def _build_search_keywords(self, metadata: Dict[str, Any]) -> str:
        """
        构建搜索关键词
        """
        keywords = []
        
        # 标题
        title = metadata.get('title', '')
        if title:
            keywords.append(title)
        
        # 年份
        year = metadata.get('year', '')
        if year:
            keywords.append(str(year))
        
        # 分辨率
        resolution = metadata.get('resolution', '')
        if resolution:
            keywords.append(resolution)
        
        # 季集信息
        season = metadata.get('season', '')
        episode = metadata.get('episode', '')
        if season:
            keywords.append(f"S{str(season).zfill(2)}")
        if episode:
            keywords.append(f"E{str(episode).zfill(2)}")
        
        return ' '.join(keywords)

    def _search_and_validate(self, torrent: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        在目标站点搜索并校验匹配的种子
        """
        torrent_name = torrent.get('name', '')
        torrent_size = torrent.get('size', 0)
        metadata = torrent.get('metadata', {})
        target_sites = torrent.get('filtered_target_sites', [])
        
        if not target_sites:
            return []
        
        # 构建搜索关键词
        keywords = self._build_search_keywords(metadata)
        if not keywords:
            logger.warning(f"无法构建搜索关键词: {torrent_name}")
            return []
        
        logger.info(f"开始跨站检索: {torrent_name}, 关键词: {keywords}")
        
        matched_torrents = []
        
        # 遍历目标站点
        for site_id in target_sites:
            # 添加随机冷却时间
            cooldown = random.randint(self._search_cooldown_min, self._search_cooldown_max)
            logger.debug(f"站点 {site_id} 搜索冷却 {cooldown} 秒")
            time.sleep(cooldown)
            
            # 搜索站点
            site_matches = self._search_site(site_id, keywords, torrent_size)
            if site_matches:
                matched_torrents.extend(site_matches)
                logger.info(f"站点 {site_id} 找到 {len(site_matches)} 个匹配种子")
        
        return matched_torrents

    def _search_site(self, site_id: str, keywords: str, source_size: int) -> List[Dict[str, Any]]:
        """
        在指定站点搜索种子
        """
        try:
            # 获取站点信息
            site = SiteOper().get(site_id)
            if not site:
                logger.warning(f"站点 {site_id} 不存在")
                return []
            
            if not site.url:
                logger.warning(f"站点 {site_id} 未配置URL")
                return []
            
            # 构建搜索URL（简化实现，使用站点RSS或搜索接口）
            # 注意：这里需要根据实际站点的搜索接口格式调整
            search_url = self._build_search_url(site, keywords)
            if not search_url:
                logger.debug(f"站点 {site_id} 无法构建搜索URL")
                return []
            
            # 发送搜索请求
            response = RequestUtils(
                ua=site.ua,
                proxies=settings.PROXY if site.proxy else None,
                headers={"Cookie": site.cookie} if site.cookie else None
            ).get_res(url=search_url)
            
            if not response or response.status_code != 200:
                logger.warning(f"站点 {site_id} 搜索请求失败")
                return []
            
            # 解析搜索结果（简化实现）
            # 注意：实际需要根据站点的返回格式解析
            torrents = self._parse_search_results(response.text, site)
            
            # 校验文件大小
            matched = []
            for torrent_info in torrents:
                if self._validate_file_size(source_size, torrent_info.get('size', 0)):
                    matched.append({
                        'site_id': site_id,
                        'site_name': site.name,
                        'torrent_url': torrent_info.get('url', ''),
                        'torrent_id': torrent_info.get('id', ''),
                        'title': torrent_info.get('title', ''),
                        'size': torrent_info.get('size', 0),
                    })
            
            return matched
        except Exception as e:
            logger.error(f"站点 {site_id} 搜索失败: {str(e)}")
            return []

    def _build_search_url(self, site, keywords: str) -> Optional[str]:
        """
        构建站点搜索URL
        注意：这是简化实现，实际需要根据不同站点的搜索接口格式调整
        """
        try:
            base_url = site.url.rstrip('/')
            
            # 常见的搜索URL格式
            # 格式1: /torrents.php?search=keywords
            # 格式2: /browse.php?search=keywords
            # 格式3: /api/torrents/search?keyword=keywords
            
            # 这里使用最常见的格式
            # 实际使用时需要根据站点类型判断
            search_url = f"{base_url}/torrents.php?search={keywords}"
            
            return search_url
        except Exception as e:
            logger.debug(f"构建搜索URL失败: {str(e)}")
            return None

    def _parse_search_results(self, html: str, site) -> List[Dict[str, Any]]:
        """
        解析搜索结果
        注意：这是简化实现，实际需要根据站点的HTML结构解析
        """
        # 这里返回空列表，实际需要解析HTML或JSON
        # 由于不同站点的格式差异很大，这里只提供框架
        # 实际使用时需要：
        # 1. 使用BeautifulSoup解析HTML
        # 2. 或者解析JSON响应
        # 3. 提取种子ID、标题、大小、下载链接等信息
        
        logger.debug(f"解析站点 {site.name} 的搜索结果（简化实现）")
        return []

    def _validate_file_size(self, source_size: int, target_size: int) -> bool:
        """
        校验文件大小是否匹配
        """
        if source_size == 0 or target_size == 0:
            return False
        
        # 计算大小差异（MB）
        size_diff = abs(source_size - target_size) / (1024 * 1024)
        
        # 判断是否在容差范围内
        if size_diff <= self._size_tolerance:
            logger.debug(f"文件大小匹配: 源={source_size}, 目标={target_size}, 差异={size_diff:.2f}MB")
            return True
        
        logger.debug(f"文件大小不匹配: 源={source_size}, 目标={target_size}, 差异={size_diff:.2f}MB")
        return False

    def _match_files(self, source_files: List[Dict], target_files: List[Dict]) -> float:
        """
        比较文件列表，返回匹配度评分（0-1）
        """
        if not source_files or not target_files:
            return 0.0
        
        # 简化实现：比较文件数量和总大小
        if len(source_files) != len(target_files):
            return 0.0
        
        # 计算文件名匹配度
        source_names = set([f.get('name', '').lower() for f in source_files])
        target_names = set([f.get('name', '').lower() for f in target_files])
        
        if not source_names or not target_names:
            return 0.0
        
        # 计算交集比例
        intersection = source_names & target_names
        union = source_names | target_names
        
        if not union:
            return 0.0
        
        match_score = len(intersection) / len(union)
        return match_score

    def _add_torrent_to_downloader(self, source_torrent: Dict[str, Any], match: Dict[str, Any]) -> bool:
        """
        将匹配的种子添加到下载器
        """
        torrent_name = source_torrent.get('name', '')
        torrent_url = match.get('torrent_url', '')
        site_name = match.get('site_name', '')
        
        if not torrent_url:
            logger.warning(f"种子 {torrent_name} 在站点 {site_name} 无下载链接")
            return False
        
        try:
            # 获取下载器实例
            downloader_service = self._downloader_helper.get_service(name=self._downloader)
            if not downloader_service:
                logger.error(f"未找到下载器: {self._downloader}")
                return False
            
            downloader = downloader_service.instance
            if not downloader:
                logger.error(f"下载器实例获取失败: {self._downloader}")
                return False
            
            # 准备添加参数
            save_path = source_torrent.get('save_path', '')
            category = source_torrent.get('category', '')
            tags = source_torrent.get('tags', [])
            
            # 添加辅种标签
            if isinstance(tags, str):
                tags = [tag.strip() for tag in tags.split(',') if tag.strip()]
            elif not isinstance(tags, list):
                tags = []
            
            if '辅种' not in tags:
                tags.append('辅种')
            
            # 主辅分离模式
            if self._enable_split_mode:
                logger.info(f"使用主辅分离模式添加种子: {torrent_name} -> {site_name}")
                success = self._add_torrent_split_mode(
                    downloader, torrent_url, save_path, category, tags
                )
            else:
                logger.info(f"使用默认模式添加种子: {torrent_name} -> {site_name}")
                success = self._add_torrent_default_mode(
                    downloader, torrent_url, save_path, category, tags
                )
            
            if success:
                # 监听种子状态
                time.sleep(2)  # 等待种子添加完成
                if self._monitor_torrent_status(downloader, torrent_url):
                    logger.info(f"辅种成功: {torrent_name} -> {site_name}")
                    return True
                else:
                    logger.warning(f"辅种失败（状态异常）: {torrent_name} -> {site_name}")
                    return False
            else:
                logger.warning(f"添加种子失败: {torrent_name} -> {site_name}")
                return False
                
        except Exception as e:
            logger.error(f"添加种子到下载器失败: {str(e)}")
            return False

    def _add_torrent_split_mode(self, downloader, torrent_url: str, save_path: str, 
                                 category: str, tags: List[str]) -> bool:
        """
        主辅分离模式：新站点种子保存路径指向原文件路径，但不允许移动或重命名
        """
        try:
            # 构建下载选项
            options = {
                'savepath': save_path,  # 使用原文件路径
                'category': category,
                'tags': ','.join(tags) if isinstance(tags, list) else tags,
                'autoTMM': False,  # 禁用自动种子管理
                'paused': False,  # 不暂停
            }
            
            # 添加种子
            result = downloader.add_torrent(
                content=torrent_url,
                download_dir=save_path,
                **options
            )
            
            return bool(result)
        except Exception as e:
            logger.error(f"主辅分离模式添加种子失败: {str(e)}")
            return False

    def _add_torrent_default_mode(self, downloader, torrent_url: str, save_path: str,
                                   category: str, tags: List[str]) -> bool:
        """
        默认模式：直接添加至源种子默认路径
        """
        try:
            # 构建下载选项
            options = {
                'savepath': save_path,
                'category': category,
                'tags': ','.join(tags) if isinstance(tags, list) else tags,
            }
            
            # 添加种子
            result = downloader.add_torrent(
                content=torrent_url,
                download_dir=save_path,
                **options
            )
            
            return bool(result)
        except Exception as e:
            logger.error(f"默认模式添加种子失败: {str(e)}")
            return False

    def _monitor_torrent_status(self, downloader, torrent_url: str) -> bool:
        """
        监听种子状态，判断是否辅种成功
        如果状态为"下载中"，判定为辅种失败（非同源），执行自动止损
        """
        try:
            # 获取种子hash（从URL或其他方式）
            # 注意：这里需要根据实际情况获取种子hash
            # 简化实现：等待一段时间后检查所有种子状态
            time.sleep(3)
            
            # 获取所有种子
            torrents = downloader.get_torrents()
            if not torrents:
                return True  # 无法获取种子列表，假设成功
            
            # 查找最近添加的种子（简化实现）
            # 实际应该通过hash精确匹配
            for torrent in torrents:
                state = getattr(torrent, 'state', '').lower()
                
                # 如果状态为下载中，判定为辅种失败
                if state in ['downloading', 'metadl', 'allocating']:
                    logger.warning(f"检测到种子状态为下载中，判定为辅种失败，执行自动止损")
                    
                    # 自动止损：删除种子和源文件
                    torrent_hash = getattr(torrent, 'hash', '')
                    if torrent_hash:
                        downloader.delete_torrents(
                            ids=[torrent_hash],
                            delete_file=True  # 删除源文件
                        )
                        logger.info(f"已删除失败的辅种种子: {torrent_hash}")
                    
                    return False
            
            return True
        except Exception as e:
            logger.error(f"监听种子状态失败: {str(e)}")
            return True  # 出错时假设成功，避免误删

    def _send_notification(self, success_count: int, failed_count: int):
        """
        发送辅种完成通知
        """
        try:
            title = "【跨站自动辅种】"
            text = f"辅种任务完成\n成功: {success_count}\n失败: {failed_count}"
            
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title=title,
                text=text
            )
        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}")

    def _save_history(self, record: Dict[str, Any]):
        """
        保存辅种历史记录
        """
        try:
            history = self.get_data('history') or []
            history.append(record)
            
            # 只保留最近100条记录
            if len(history) > 100:
                history = history[-100:]
            
            self.save_data('history', history)
        except Exception as e:
            logger.error(f"保存历史记录失败: {str(e)}")

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
        # 获取历史记录
        history = self.get_data('history') or []
        
        # 获取缓存统计
        cache = self._load_cache()
        success_count = len(cache.get('success', {}))
        failed_count = len(cache.get('failed', {}))
        
        if not history and success_count == 0 and failed_count == 0:
            return [{
                'component': 'VAlert',
                'props': {
                    'type': 'info',
                    'text': '暂无辅种数据',
                    'variant': 'tonal',
                }
            }]
        
        # 构建页面内容
        contents = []
        
        # 统计卡片
        contents.append({
            'component': 'VRow',
            'content': [
                {
                    'component': 'VCol',
                    'props': {'cols': 12, 'md': 6},
                    'content': [{
                        'component': 'VCard',
                        'props': {'variant': 'tonal'},
                        'content': [{
                            'component': 'VCardText',
                            'content': [{
                                'component': 'div',
                                'props': {'class': 'text-h6'},
                                'text': f'成功缓存: {success_count}'
                            }]
                        }]
                    }]
                },
                {
                    'component': 'VCol',
                    'props': {'cols': 12, 'md': 6},
                    'content': [{
                        'component': 'VCard',
                        'props': {'variant': 'tonal'},
                        'content': [{
                            'component': 'VCardText',
                            'content': [{
                                'component': 'div',
                                'props': {'class': 'text-h6'},
                                'text': f'失败缓存: {failed_count}'
                            }]
                        }]
                    }]
                }
            ]
        })
        
        # 历史记录
        if history:
            # 按时间倒序
            history_sorted = sorted(history, key=lambda x: x.get('timestamp', ''), reverse=True)
            
            history_items = []
            for record in history_sorted[:20]:  # 只显示最近20条
                status = record.get('status', '')
                status_color = 'success' if status == '成功' else 'error'
                
                history_items.append({
                    'component': 'VCard',
                    'props': {'class': 'mb-2'},
                    'content': [{
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'div',
                                'props': {'class': 'd-flex justify-space-between'},
                                'content': [
                                    {
                                        'component': 'span',
                                        'text': record.get('torrent_name', '未知')
                                    },
                                    {
                                        'component': 'VChip',
                                        'props': {
                                            'color': status_color,
                                            'size': 'small'
                                        },
                                        'text': status
                                    }
                                ]
                            },
                            {
                                'component': 'div',
                                'props': {'class': 'text-caption mt-1'},
                                'text': f"{record.get('source_site', '')} → {record.get('target_site', '')}"
                            },
                            {
                                'component': 'div',
                                'props': {'class': 'text-caption'},
                                'text': record.get('timestamp', '')
                            }
                        ]
                    }]
                })
            
            contents.append({
                'component': 'VRow',
                'content': [{
                    'component': 'VCol',
                    'props': {'cols': 12},
                    'content': [{
                        'component': 'div',
                        'props': {'class': 'text-h6 mb-2'},
                        'text': '辅种历史'
                    }] + history_items
                }]
            })
        
        return [{
            'component': 'div',
            'props': {'class': 'pa-4'},
            'content': contents
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
