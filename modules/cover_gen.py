import requests
import base64
from openai import OpenAI


def _wb_image_url(nm):
    nm = int(nm)
    vol = nm // 100000
    part = nm // 1000
    baskets = [
        (143, '01'), (287, '02'), (431, '03'), (719, '04'),
        (1007, '05'), (1061, '06'), (1115, '07'), (1169, '08'),
        (1313, '09'), (1601, '10'), (1655, '11'), (1919, '12'),
        (2045, '13'), (2189, '14'), (2405, '15'), (2621, '16'),
        (2837, '17'),
    ]
    basket = '18'
    for threshold, b in baskets:
        if vol <= threshold:
            basket = b
            break
    return f'https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/1.jpg'


def search_wb_covers(keyword, limit=8):
    """Search WB products by keyword, return cover URLs."""
    try:
        r = requests.get(
            'https://search.wb.ru/exactmatch/ru/common/v9/search',
            params={
                'appType': 1, 'curr': 'rub', 'dest': -1257786,
                'query': keyword, 'resultset': 'catalog',
                'sort': 'popular', 'spp': 30,
            },
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=12
        )
        if r.status_code != 200:
            return []
        products = r.json().get('data', {}).get('products', [])[:limit]
        return [
            {
                'nm': p['id'],
                'name': p.get('name', ''),
                'brand': p.get('brand', ''),
                'url': _wb_image_url(p['id']),
                'price': p.get('priceU', 0) // 100,
            }
            for p in products
        ]
    except Exception:
        return []


def analyze_and_generate(keyword, product_name, openai_key, n_competitors=6):
    """Fetch competitor covers, analyze with GPT-4o Vision, generate with DALL-E 3."""
    client = OpenAI(api_key=openai_key)

    covers = search_wb_covers(keyword, limit=n_competitors)

    image_content = []
    valid_covers = []
    for c in covers:
        try:
            resp = requests.get(c['url'], timeout=8,
                                headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code == 200:
                b64 = base64.b64encode(resp.content).decode()
                ext = 'jpeg'
                image_content.append({
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/{ext};base64,{b64}', 'detail': 'low'}
                })
                valid_covers.append(c)
        except Exception:
            continue

    analysis_prompt = f"""Ты эксперт по визуальному маркетингу на маркетплейсах.

Проанализируй обложки конкурентов в нише "{keyword}" на Wildberries.
Мой товар: {product_name}

Задача — понять, что делает обложку кликабельной (высокий CTR).

Определи:
1. Цветовые схемы топ-товаров (фон, акценты)
2. Типографика и текстовые бейджи (скидки, преимущества, цифры)
3. Расположение товара на кадре (центр, угол, фронт/бок)
4. Ключевые визуальные триггеры (до/после, количество, состав)
5. Что отличает хорошие обложки от плохих

Затем напиши детальный промпт для DALL-E 3 на английском для генерации обложки моего товара с максимальным CTR.

Формат ответа:
АНАЛИЗ:
[3-5 конкретных инсайтов]

ПРОМПТ:
[DALL-E 3 prompt на английском, 200-400 слов]"""

    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': analysis_prompt}] + image_content}]

    analysis_resp = client.chat.completions.create(
        model='gpt-4o',
        messages=messages,
        max_tokens=1000
    )
    analysis_text = analysis_resp.choices[0].message.content

    dalle_prompt = ''
    if 'ПРОМПТ:' in analysis_text:
        dalle_prompt = analysis_text.split('ПРОМПТ:')[-1].strip()
    if not dalle_prompt:
        dalle_prompt = (
            f'Professional product photo for Russian e-commerce marketplace Wildberries. '
            f'Product: {product_name}. Category: {keyword}. '
            f'White background, sharp focus, high contrast, eye-catching composition, '
            f'bold accent colors, clear product showcase, photorealistic, studio lighting.'
        )

    img_resp = client.images.generate(
        model='dall-e-3',
        prompt=dalle_prompt[:4000],
        size='1024x1024',
        quality='standard',
        n=1
    )

    return {
        'covers': valid_covers,
        'analysis': analysis_text,
        'dalle_prompt': dalle_prompt,
        'generated_url': img_resp.data[0].url,
    }
