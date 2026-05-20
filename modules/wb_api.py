import requests
from datetime import datetime, timedelta

ADVERT_API = 'https://advert-api.wildberries.ru'
CONTENT_API = 'https://content-api.wildberries.ru'


def _advert_headers(key):
    return {'Authorization': key, 'Content-Type': 'application/json'}


def _content_headers(key):
    token = key.strip()
    if not token.lower().startswith('bearer '):
        token = f'Bearer {token}'
    return {'Authorization': token, 'Content-Type': 'application/json'}


def get_active_campaigns(api_key):
    """Список активных рекламных кампаний (status=9)."""
    try:
        r = requests.get(
            f'{ADVERT_API}/adv/v1/promotion/adverts',
            params={'status': 9, 'limit': 100, 'offset': 0},
            headers=_advert_headers(api_key),
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else []
        return []
    except Exception:
        return []


def get_all_campaigns(api_key):
    """Все кампании всех статусов (активные + приостановленные)."""
    results = []
    try:
        # status: 4=готова, 7=завершена, 8=отказ, 9=активна, 11=пауза
        for status in [9, 11, 4]:
            r = requests.get(
                f'{ADVERT_API}/adv/v1/promotion/adverts',
                params={'status': status, 'limit': 100, 'offset': 0},
                headers=_advert_headers(api_key),
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    results.extend(data)
    except Exception:
        pass
    return results


def get_campaigns_ctr(api_key, campaign_ids, days=30):
    """Статистика CTR для списка кампаний за N дней. Возвращает dict {id: stats}."""
    if not campaign_ids:
        return {}
    today = datetime.now().strftime('%Y-%m-%d')
    date_from = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    try:
        payload = [{'id': int(cid), 'interval': {'begin': date_from, 'end': today}}
                   for cid in campaign_ids[:50]]
        r = requests.post(
            f'{ADVERT_API}/adv/v2/fullstats',
            json=payload,
            headers=_advert_headers(api_key),
            timeout=20
        )
        if r.status_code != 200:
            return {}
        result = {}
        for item in (r.json() or []):
            cid = item.get('advertId')
            if not cid:
                continue
            views = item.get('views', 0)
            clicks = item.get('clicks', 0)
            ctr = round(item.get('ctr', 0.0), 2)
            spent = round(item.get('sum', 0.0), 2)
            result[cid] = {
                'views': views,
                'clicks': clicks,
                'ctr': ctr,
                'spent': spent,
            }
        return result
    except Exception:
        return {}


def get_campaign_stats(api_key, campaign_id, days=7):
    """Статистика одной кампании за последние N дней."""
    stats = get_campaigns_ctr(api_key, [campaign_id], days=days)
    return stats.get(int(campaign_id))


def set_product_photo(api_key, vendor_code, photo_path, photo_number=1, client_secret=None):
    """Установить фото товара как обложку через Content API v3."""
    try:
        with open(photo_path, 'rb') as f:
            ext = photo_path.rsplit('.', 1)[-1].lower()
            mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'
            token = api_key.strip()
            auth_variants = []
            if token.lower().startswith('bearer '):
                plain = token[7:]
                auth_variants = [(plain, True), (token, False)]
            else:
                auth_variants = [(token, True), (f'Bearer {token}', False)]

            last_error = 'Unknown error'
            for auth_value, use_secret in auth_variants:
                f.seek(0)
                headers = {'Authorization': auth_value}
                if client_secret and use_secret:
                    headers['X-Client-Secret'] = client_secret
                r = requests.post(
                    f'{CONTENT_API}/content/v3/media/save',
                    params={'vendorCode': vendor_code, 'photoNumber': photo_number},
                    files={'uploadfile': (f'photo.{ext}', f, mime)},
                    headers=headers,
                    timeout=30
                )
                if r.status_code == 200:
                    return True, 'OK'
                last_error = r.text
                if r.status_code not in (401, 403):
                    break
            return False, last_error
    except Exception as e:
        return False, str(e)



def set_product_photo(api_key, vendor_code, photo_path, photo_number=1, client_secret=None):
    """Установить фото товара как обложку через Content API v3."""
    try:
        with open(photo_path, 'rb') as f:
            ext = photo_path.rsplit('.', 1)[-1].lower()
            mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'
            token = api_key.strip()
            # WB Content API v3 (s2s): token without Bearer + X-Client-Secret
            # Try plain token first, then with Bearer prefix
            auth_variants = []
            if token.lower().startswith('bearer '):
                plain = token[7:]
                auth_variants = [(plain, True), (token, False)]
            else:
                auth_variants = [(token, True), (f'Bearer {token}', False)]

            last_error = 'Unknown error'
            for auth_value, use_secret in auth_variants:
                f.seek(0)
                headers = {'Authorization': auth_value}
                if client_secret and use_secret:
                    headers['X-Client-Secret'] = client_secret
                r = requests.post(
                    f'{CONTENT_API}/content/v3/media/save',
                    params={'vendorCode': vendor_code, 'photoNumber': photo_number},
                    files={'uploadfile': (f'photo.{ext}', f, mime)},
                    headers=headers,
                    timeout=30
                )
                if r.status_code == 200:
                    return True, 'OK'
                last_error = r.text
                if r.status_code not in (401, 403):
                    break
            return False, last_error
    except Exception as e:
        return False, str(e)
