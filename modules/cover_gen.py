import requests
import base64
from io import BytesIO
from PIL import Image as PILImage
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


def _load_competitor_images(covers):
    """Download competitor images, return (image_content_list, valid_covers)."""
    image_content = []
    valid_covers = []
    for c in covers:
        try:
            resp = requests.get(c['url'], timeout=8,
                                headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code == 200:
                b64 = base64.b64encode(resp.content).decode()
                image_content.append({
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{b64}', 'detail': 'low'}
                })
                valid_covers.append(c)
        except Exception:
            continue
    return image_content, valid_covers


def _to_png_bytes(image_bytes):
    """Convert any image bytes to PNG bytes (required by gpt-image-1 edit)."""
    img = PILImage.open(BytesIO(image_bytes)).convert('RGBA')
    out = BytesIO()
    img.save(out, format='PNG')
    out.seek(0)
    return out


def analyze_competitors(keyword, product_name, openai_key, competitor_images, extra_hint=''):
    """Use GPT-4o to analyze competitor covers and produce a generation prompt."""
    client = OpenAI(api_key=openai_key)

    hint_line = f'\nДополнительно учти: {extra_hint}' if extra_hint else ''
    analysis_prompt = f"""Ты эксперт по визуальному маркетингу на маркетплейсах.

Проанализируй обложки конкурентов в нише "{keyword}" на Wildberries.
Мой товар: {product_name}{hint_line}

Определи что делает обложку кликабельной (высокий CTR):
1. Цветовые схемы и фоны топ-товаров
2. Текстовые бейджи и акценты (цифры, выгоды, скидки)
3. Расположение товара в кадре
4. Ключевые визуальные триггеры
5. Главное отличие хороших обложек от плохих

Затем напиши промпт на английском для создания обложки моего товара с максимальным CTR.
Промпт должен описывать фон, расположение, текст-оверлеи, цвета — но НЕ описывать сам товар
(товар будет вставлен автоматически из фото).

Формат ответа:
АНАЛИЗ:
[3-5 конкретных инсайтов]

ПРОМПТ:
[prompt на английском, 150-300 слов]"""

    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': analysis_prompt}] + competitor_images}]
    resp = client.chat.completions.create(model='gpt-4o', messages=messages, max_tokens=900)
    return resp.choices[0].message.content


def analyze_and_generate(keyword, product_name, openai_key,
                         product_image_bytes=None, extra_hint='', n_competitors=6):
    """Full pipeline: parse WB → analyze → generate cover with real product photo."""
    client = OpenAI(api_key=openai_key)

    covers = search_wb_covers(keyword, limit=n_competitors)
    competitor_images, valid_covers = _load_competitor_images(covers)

    analysis_text = analyze_competitors(
        keyword, product_name, openai_key, competitor_images, extra_hint
    )

    dalle_prompt = ''
    if 'ПРОМПТ:' in analysis_text:
        dalle_prompt = analysis_text.split('ПРОМПТ:')[-1].strip()
    if not dalle_prompt:
        dalle_prompt = (
            f'Professional Wildberries marketplace product cover. '
            f'Category: {keyword}. Eye-catching composition, bold accent colors, '
            f'white or gradient background, studio lighting, photorealistic.'
        )

    if product_image_bytes:
        # Use gpt-image-1 edit — places the real product into the generated scene
        full_prompt = (
            f'Create a professional Wildberries marketplace cover image. '
            f'Keep the product from the reference image exactly as-is (same shape, label, packaging). '
            f'Apply this cover design: {dalle_prompt[:2000]}'
        )
        png_file = _to_png_bytes(product_image_bytes)
        img_resp = client.images.edit(
            model='gpt-image-1',
            image=('product.png', png_file, 'image/png'),
            prompt=full_prompt[:4000],
            size='1024x1024',
        )
        # gpt-image-1 returns base64
        img_b64 = img_resp.data[0].b64_json
        generated_url = f'data:image/png;base64,{img_b64}'
    else:
        img_resp = client.images.generate(
            model='dall-e-3',
            prompt=dalle_prompt[:4000],
            size='1024x1024',
            quality='standard',
            n=1,
        )
        generated_url = img_resp.data[0].url

    return {
        'covers': valid_covers,
        'analysis': analysis_text,
        'dalle_prompt': dalle_prompt,
        'generated_url': generated_url,
        'used_product_photo': bool(product_image_bytes),
    }
