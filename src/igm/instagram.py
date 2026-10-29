"""Official Meta Instagram API adapter (Instagram Login by default).

No login scraping, no passwords, no unofficial Instagram APIs.
Meta app review and scopes may be required before production use.
"""
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class InstagramError(RuntimeError):
    pass


class InstagramAPI:
    def __init__(self, account_id=None, access_token=None, version=None, transport=None):
        self.account_id = account_id or os.getenv('IG_USER_ID', '')
        self.token = access_token or os.getenv('IG_ACCESS_TOKEN', '')
        self.version = version or os.getenv('META_GRAPH_VERSION', 'v26.0')
        self.auth_mode = os.getenv('IG_AUTH_MODE', 'instagram').strip().lower()
        if self.auth_mode not in ('instagram', 'facebook'):
            raise InstagramError('IG_AUTH_MODE must be instagram or facebook')
        self.expected_username = os.getenv('IG_EXPECTED_USERNAME', 'mybrand').strip().lstrip('@').lower()
        self.transport = transport or urlopen
        if not self.version.startswith('v') or not self.version[1:].replace('.', '').isdigit():
            raise InstagramError('META_GRAPH_VERSION has invalid format')
        if not self.account_id.isdigit():
            raise InstagramError('IG_USER_ID must be a numeric professional account ID')
        if not self.token:
            raise InstagramError('IG_ACCESS_TOKEN is missing')
        host = 'graph.instagram.com' if self.auth_mode == 'instagram' else 'graph.facebook.com'
        self.base = f'https://{host}/{self.version}'

    def request(self, path, *, params=None, post=None, delete=False):
        qs = ('?' + urlencode(params)) if params else ''
        url = self.base + '/' + str(path).lstrip('/') + qs
        body = urlencode(post).encode('utf-8') if post is not None else None
        if delete and post is not None:
            raise InstagramError('Cannot DELETE and POST in the same request')
        req = Request(url, data=body, headers={
            'Authorization': 'Bearer ' + self.token,
            'Accept': 'application/json',
            'User-Agent': 'MyBrand-AI-Growth-Manager/0.1',
        }, method='DELETE' if delete else ('POST' if post is not None else 'GET'))
        try:
            with self.transport(req, timeout=25) as response:
                payload = json.load(response)
        except HTTPError as ex:
            # Never echo tokens or URLs (they may contain credentials).
            try:
                detail = json.loads(ex.read().decode('utf-8')).get('error', {})
                code = detail.get('code', 'unknown')
                subcode = detail.get('error_subcode', '')
            except (ValueError, UnicodeError):
                code, subcode = 'unknown', ''
            raise InstagramError(f'Meta API returned HTTP {ex.code}; error code {code}, subcode {subcode}') from None
        except (URLError, TimeoutError) as ex:
            raise InstagramError(f'Network request failed ({type(ex).__name__})') from None
        except ValueError:
            raise InstagramError('Meta API returned malformed JSON') from None
        if 'error' in payload:
            raise InstagramError(f'Meta API error code {payload["error"].get("code", "unknown")}')
        return payload

    def profile(self):
        return self.request(self.account_id, params={
            'fields': 'id,username,followers_count,media_count'
        })

    def assert_correct_account(self):
        """Block writes when an access token belongs to an unexpected account."""
        actual = str(self.profile().get('username', '')).strip().lstrip('@').lower()
        if not actual or actual != self.expected_username:
            raise InstagramError('Instagram account username mismatch; publication blocked')
        return actual

    def recent_media(self, limit=25):
        return self.request(self.account_id + '/media', params={
            'fields': 'id,caption,media_type,permalink,timestamp,like_count,comments_count',
            'limit': max(1, min(int(limit), 50)),
        }).get('data', [])

    def list_comments(self, media_id, after=None):
        """Fetch a bounded page of comment data for media owned by our account."""
        if not str(media_id).isdigit():
            raise InstagramError('Invalid media ID')
        query = {'fields': 'id,username,text,timestamp', 'limit': 100}
        if after:
            query['after'] = after
        return self.request(str(media_id) + '/comments', params=query)

    def delete_comment(self, comment_id):
        """Permanently delete a comment on our owned media (requires manage_comments)."""
        if not str(comment_id).isdigit():
            raise InstagramError('Invalid comment ID')
        self.assert_correct_account()
        result = self.request(str(comment_id), delete=True)
        if result.get('success') is not True:
            raise InstagramError('Meta did not confirm deletion of comment')
        return True

    def publish_image(self, image_url, caption):
        # Image must be fetchable by Meta over HTTPS, from a publicly available URL.
        from urllib.parse import urlsplit
        parts = urlsplit(image_url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise InstagramError('image_url must be a public HTTPS address without credentials or custom port')
        if len(caption) > 2200:
            raise InstagramError('Instagram caption is too long (2200-character limit)')
        self.assert_correct_account()
        creation = self.request(self.account_id + '/media', post={
            'image_url': image_url, 'caption': caption
        })
        container_id = str(creation.get('id', ''))
        if not container_id.isdigit():
            raise InstagramError('Meta did not return an Instagram media container ID')
        for attempt in range(6):
            current = self.request(container_id, params={'fields': 'status_code'})
            state = current.get('status_code', '')
            if state == 'FINISHED':
                break
            if state in ('ERROR', 'EXPIRED'):
                raise InstagramError(f'Instagram media container status: {state}')
            if attempt < 5:
                time.sleep(3)
        else:
            raise InstagramError('Media container is still processing; nothing was published')
        publication = self.request(self.account_id + '/media_publish', post={'creation_id': container_id})
        media_id = str(publication.get('id', ''))
        if not media_id.isdigit():
            raise InstagramError('Meta did not confirm a published media ID; check in Instagram')
        return {'media_id': media_id, 'container_id': container_id}


    # ---------- Пайплайн MyBrand: карусели и Reels ----------
    @staticmethod
    def _check_public_url(url):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise InstagramError('Media URL must be public HTTPS without credentials or custom port')

    def _container(self, post):
        created = self.request(self.account_id + '/media', post=post)
        cid = str(created.get('id', ''))
        if not cid.isdigit():
            raise InstagramError('Meta did not return an Instagram media container ID')
        return cid

    def wait_container(self, container_id, attempts=40, delay=5, sleep=time.sleep):
        """Video containers can take a minute or more to process."""
        for attempt in range(attempts):
            state = self.request(container_id, params={'fields': 'status_code'}).get('status_code', '')
            if state == 'FINISHED':
                return True
            if state in ('ERROR', 'EXPIRED'):
                raise InstagramError(f'Instagram media container status: {state}')
            if attempt < attempts - 1:
                sleep(delay)
        raise InstagramError('Media container is still processing; nothing was published')

    def publish_container(self, container_id):
        publication = self.request(self.account_id + '/media_publish', post={'creation_id': container_id})
        media_id = str(publication.get('id', ''))
        if not media_id.isdigit():
            raise InstagramError('Meta did not confirm a published media ID; check in Instagram')
        link = ''
        try:
            link = str(self.request(media_id, params={'fields': 'permalink'}).get('permalink', ''))
        except InstagramError:
            pass
        return {'media_id': media_id, 'container_id': container_id, 'permalink': link}

    def publish_carousel(self, image_urls, caption, sleep=time.sleep):
        if not 2 <= len(image_urls) <= 10:
            raise InstagramError('Carousel needs 2 to 10 images')
        if len(caption) > 2200:
            raise InstagramError('Instagram caption is too long (2200-character limit)')
        for url in image_urls:
            self._check_public_url(url)
        self.assert_correct_account()
        children = []
        for url in image_urls:
            cid = self._container({'image_url': url, 'is_carousel_item': 'true'})
            self.wait_container(cid, attempts=12, delay=3, sleep=sleep)
            children.append(cid)
        parent = self._container({'media_type': 'CAROUSEL', 'children': ','.join(children), 'caption': caption})
        self.wait_container(parent, attempts=12, delay=3, sleep=sleep)
        return self.publish_container(parent)

    def publish_single_image(self, image_url, caption, sleep=time.sleep):
        self._check_public_url(image_url)
        if len(caption) > 2200:
            raise InstagramError('Instagram caption is too long (2200-character limit)')
        self.assert_correct_account()
        cid = self._container({'image_url': image_url, 'caption': caption})
        self.wait_container(cid, attempts=12, delay=3, sleep=sleep)
        return self.publish_container(cid)

    def publish_reel(self, video_url, caption, cover_url=None, sleep=time.sleep):
        self._check_public_url(video_url)
        if len(caption) > 2200:
            raise InstagramError('Instagram caption is too long (2200-character limit)')
        self.assert_correct_account()
        post = {'media_type': 'REELS', 'video_url': video_url, 'caption': caption, 'share_to_feed': 'true'}
        if cover_url:
            self._check_public_url(cover_url)
            post['cover_url'] = cover_url
        cid = self._container(post)
        self.wait_container(cid, attempts=40, delay=6, sleep=sleep)
        return self.publish_container(cid)

    def publishing_limit(self):
        """Needs instagram_business_content_publish; a permission probe without posting."""
        data = self.request(self.account_id + '/content_publishing_limit', params={'fields': 'config,quota_usage'})
        rows = data.get('data') or [{}]
        return rows[0]


    # ---------- Insights для Viral Lab ----------
    FEED_METRICS = ('reach', 'views', 'saved', 'shares', 'likes', 'comments', 'total_interactions')
    REELS_METRICS = ('reach', 'views', 'saved', 'shares', 'likes', 'comments', 'total_interactions',
                     'ig_reels_avg_watch_time', 'ig_reels_video_view_total_time')

    def media_insights(self, media_id, media_type=''):
        """Lifetime metrics of our own media. Needs instagram_business_manage_insights.
        Unsupported metrics are dropped one by one instead of failing the whole request."""
        if not str(media_id).isdigit():
            raise InstagramError('Invalid media ID')
        metrics = list(self.REELS_METRICS if str(media_type).upper() in ('REELS', 'VIDEO') else self.FEED_METRICS)
        while metrics:
            try:
                data = self.request(str(media_id) + '/insights', params={'metric': ','.join(metrics)})
                out = {}
                for row in data.get('data', []):
                    values = row.get('values') or [{}]
                    value = row.get('total_value', {}).get('value', values[0].get('value'))
                    if isinstance(value, (int, float)):
                        out[row.get('name')] = value
                return out
            except InstagramError as err:
                text = str(err)
                if 'error code 10,' in text or 'error code 190' in text or 'error code 200' in text:
                    raise  # permission / token problems: let caller fall back
                if len(metrics) <= 2:
                    raise
                metrics.pop()  # drop the least important metric and retry
        return {}

    def media_basic(self, media_id):
        if not str(media_id).isdigit():
            raise InstagramError('Invalid media ID')
        data = self.request(str(media_id), params={'fields': 'like_count,comments_count,media_type,timestamp,permalink'})
        return {'likes': data.get('like_count'), 'comments': data.get('comments_count')}

    def insights_probe(self):
        """Account-level check whether insights permission is granted."""
        self.request(self.account_id + '/insights', params={'metric': 'reach', 'period': 'day'})
        return True

    def follower_demographics(self, breakdown='age'):
        """Available from 100 followers; returns {bucket: count} or raises."""
        data = self.request(self.account_id + '/insights', params={
            'metric': 'follower_demographics', 'period': 'lifetime', 'metric_type': 'total_value', 'breakdown': breakdown})
        out = {}
        for row in data.get('data', []):
            for b in (row.get('total_value', {}).get('breakdowns') or []):
                for r in b.get('results', []):
                    key = '/'.join(str(x) for x in r.get('dimension_values', []))
                    out[key] = r.get('value')
        return out
