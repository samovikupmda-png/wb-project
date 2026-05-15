import requests
from datetime import datetime, timedelta

ADVERT_API = 'https://advert-api.wildberries.ru'
CONTENT_API = 'https://content-api.wildberries.ru'


def _ah(key):
    return {'Authorization': key, 'Content-Type': 'application/json'}


def get_active_campaigns(api_key):
    """Список активных рекламных кампаний (status=9)."""
    try:
        r = requests.get(
            f'{ADVERT_API}/adv/v1/promotion/adverts',
            params={'status': 9, 'limit': 100, 'offset': 0},
            headers={'Authorization': api_key},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else []
        return []
    except Exception:
        return []


def get_campaign_stats(api_key, campaign_id, days=7):
    """Статистика кампании за последние N дней."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        date_from = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        r = requests.post(
            f'{ADVERT_API}/adv/v2/fullstats',
            json=[{'id': int(campaign_id), 'interval': {'begin': date_from, 'end': today}}],
            headers=_ah(api_key),
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                stat = data[0]
                return {
                    'views': stat.get('views', 0),
                    'clicks': stat.get('clicks', 0),
                    'ctr': round(stat.get('ctr', 0.0), 2),
                }
        return None
    except Exception:
        return None


def set_product_photo(api_key, vendor_code, photo_path, photo_number=1):
    """Установить фото товара как обложку (photo_number=1 = главное фото)."""
    try:
        with open(photo_path, 'rb') as f:
            ext = photo_path.rsplit('.', 1)[-1].lower()
            mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'
            r = requests.post(
                f'{CONTENT_API}/content/v3/media/save',
                params={'vendorCode': vendor_code, 'photoNumber': photo_number},
                files={'uploadfile': (f'photo.{ext}', f, mime)},
                headers={'Authorization': api_key},
                timeout=30
            )
            return r.status_code == 200, r.text
    except Exception as e:
        return False, str(e)
