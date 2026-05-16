import requests
import base64
from io import BytesIO
from PIL import Image as PILImage
from openai import OpenAI

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def _calc_basket(vol):
    """Calculate WB CDN basket number from vol (nm // 100000)."""
    # Fixed ranges for baskets 01-17
    fixed = [
        (143, 1), (287, 2), (431, 3), (719, 4), (1007, 5),
        (1061, 6), (1115, 7), (1169, 8), (1313, 9), (1601, 10),
        (1655, 11), (1919, 12), (2045, 13), (2189, 14), (2405, 15),
        (2621, 16), (2837, 17),
    ]
    for threshold, b in fixed:
        if vol <= threshold:
            return str(b).zfill(2)
    # From basket-18 onwards: each basket covers 216 vol units, starting at 2838
    n = 18 + (vol - 2838) // 216
    return str(n).zfill(2)


def _wb_image_url(nm, photo_number=1, basket=None):
    nm = int(nm)
    vol = nm // 100000
    part = nm // 1000
    if basket is None:
        basket = _calc_basket(vol)
    return f'https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{photo_number}.jpg'


def fetch_product_photos(article, max_photos=5):
    """Fetch all available product photos from WB CDN by article number."""
    nm = int(article)
    vol = nm // 100000
    basket = _calc_basket(vol)
    photos = []
    for i in range(1, max_photos + 1):
        url = _wb_image_url(nm, photo_number=i, basket=basket)
        try:
            r = requests.get(url, timeout=8, headers=HEADERS)
            if r.status_code == 200 and len(r.content) > 5000:
                photos.append({'number': i, 'url': url, 'bytes': r.content})
            else:
                break
        except Exception:
            break
    return photos


def fetch_wb_cover_by_article(article):
    """Fetch the current cover (photo #1) of a WB product."""
    photos = fetch_product_photos(article, max_photos=1)
    return photos[0]['bytes'] if photos else None


def search_wb_covers(keyword, limit=8):
    try:
        r = requests.get(
            'https://search.wb.ru/exactmatch/ru/common/v9/search',
            params={
                'appType': 1, 'curr': 'rub', 'dest': -1257786,
                'query': keyword, 'resultset': 'catalog',
                'sort': 'popular', 'spp': 30,
            },
            headers=HEADERS,
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


def _load_competitor_images(covers, max_load=4):
    image_content = []
    valid_covers = []
    for c in covers[:max_load]:
        try:
            resp = requests.get(c['url'], timeout=6, headers=HEADERS)
            if resp.status_code == 200 and len(resp.content) > 3000:
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
    img = PILImage.open(BytesIO(image_bytes)).convert('RGBA')
    out = BytesIO()
    img.save(out, format='PNG')
    out.seek(0)
    return out


def extract_cover_info(current_cover_bytes, openai_key):
    """Use GPT-4o to extract all text and product info from the current cover."""
    client = OpenAI(api_key=openai_key)
    b64 = base64.b64encode(current_cover_bytes).decode()
    resp = client.chat.completions.create(
        model='gpt-4o',
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'text',
                    'text': """Внимательно прочитай эту обложку товара и выпиши ВЕСЬ текст и информацию с неё.

Включи абсолютно всё:
- Название товара и бренд
- Количество (капсул, штук, граммов, мл и т.д.)
- Дозировку и состав если указаны
- Все маркетинговые claims (например: "-7 кг за 14 дней", "100% натуральный", "без ГМО")
- Сертификаты или знаки качества
- Любые цифры, даты, проценты
- Любой другой текст с обложки

Формат ответа:
ИНФОРМАЦИЯ С ОБЛОЖКИ:
[Весь найденный текст и данные, по пунктам]

КЛЮЧЕВЫЕ CLAIMS ДЛЯ НОВОЙ ОБЛОЖКИ:
[Самые важные маркетинговые утверждения, которые ОБЯЗАТЕЛЬНО должны быть на новой обложке]"""
                },
                {
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{b64}', 'detail': 'high'}
                }
            ]
        }],
        max_tokens=600
    )
    return resp.choices[0].message.content


def analyze_and_generate(keyword, product_name, openai_key,
                         article=None,
                         product_photo_number=1,
                         extra_hint='',
                         n_competitors=6):
    """
    Full pipeline:
    1. Auto-fetch current cover + product photo from WB by article
    2. Extract all text/info from current cover via GPT-4o
    3. Parse competitor covers from WB search
    4. Analyze competitors + build prompt with correct product info
    5. Generate new cover with gpt-image-1 using real product photo
    """
    client = OpenAI(api_key=openai_key)

    # Step 1: Fetch current cover and product photo from WB
    current_cover_bytes = None
    product_image_bytes = None
    fetched_photos = []

    if article:
        fetched_photos = fetch_product_photos(article, max_photos=6)
        if fetched_photos:
            current_cover_bytes = fetched_photos[0]['bytes']  # cover = photo #1
            # Use selected photo number as product image (default: cover)
            selected = next((p for p in fetched_photos if p['number'] == product_photo_number),
                            fetched_photos[0])
            product_image_bytes = selected['bytes']

    # Step 2: Extract info from current cover
    cover_info = ''
    if current_cover_bytes:
        cover_info = extract_cover_info(current_cover_bytes, openai_key)

    # Step 3: Fetch competitor covers
    covers = search_wb_covers(keyword, limit=n_competitors)
    competitor_images, valid_covers = _load_competitor_images(covers)

    # Step 4: Build analysis prompt
    cover_info_block = (
        f'\n\nИНФОРМАЦИЯ С ТЕКУЩЕЙ ОБЛОЖКИ (взята с WB автоматически):\n{cover_info}\n'
        f'Эту информацию (количество, claims, состав) ОБЯЗАТЕЛЬНО включи в промпт для генерации.'
    ) if cover_info else ''
    hint_line = f'\nДополнительно: {extra_hint}' if extra_hint else ''

    analysis_prompt = f"""Ты эксперт по визуальному маркетингу на маркетплейсах.

Проанализируй обложки конкурентов в нише "{keyword}" на Wildberries.
Мой товар: {product_name}{cover_info_block}{hint_line}

Определи что делает обложку кликабельной (высокий CTR):
1. Цветовые схемы и фоны топ-товаров
2. Текстовые бейджи и акценты (цифры, выгоды, скидки)
3. Расположение товара в кадре
4. Ключевые визуальные триггеры

Затем напиши детальный промпт на английском для создания обложки моего товара.
Промпт ОБЯЗАН включать всю текстовую информацию с текущей обложки (количество, состав, claims).
Промпт описывает фон, расположение, текст-оверлеи, цвета. Товар будет вставлен из фото автоматически.

Формат:
АНАЛИЗ:
[3-5 конкретных инсайтов]

ПРОМПТ:
[prompt на английском, 200-350 слов, обязательно с точными текстами с обложки]"""

    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': analysis_prompt}] + competitor_images}]
    analysis_resp = client.chat.completions.create(model='gpt-4o', messages=messages, max_tokens=1000)
    analysis_text = analysis_resp.choices[0].message.content

    dalle_prompt = ''
    if 'ПРОМПТ:' in analysis_text:
        dalle_prompt = analysis_text.split('ПРОМПТ:')[-1].strip()
    if not dalle_prompt:
        dalle_prompt = (
            f'Professional Wildberries marketplace product cover for {product_name}. '
            f'Category: {keyword}. Eye-catching composition, bold text badges, '
            f'white or gradient background, studio lighting, photorealistic.'
        )

    # Step 5: Generate with real product photo
    if product_image_bytes:
        full_prompt = (
            f'Create a professional Wildberries marketplace cover image. '
            f'Keep the product from the reference image exactly as-is — '
            f'same packaging, label, shape, colors, all text on the product. '
            f'Apply this cover design around it: {dalle_prompt[:2500]}'
        )
        png_file = _to_png_bytes(product_image_bytes)
        try:
            img_resp = client.images.edit(
                model='gpt-image-1',
                image=('product.png', png_file, 'image/png'),
                prompt=full_prompt[:4000],
                size='1024x1024',
                timeout=180,
            )
            img_b64 = img_resp.data[0].b64_json
            generated_url = f'data:image/png;base64,{img_b64}'
        except Exception:
            # Fallback to DALL-E 3 if gpt-image-1 unavailable
            img_resp = client.images.generate(
                model='dall-e-3',
                prompt=dalle_prompt[:4000],
                size='1024x1024',
                quality='standard',
                n=1,
            )
            generated_url = img_resp.data[0].url
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
        'cover_info': cover_info,
        'analysis': analysis_text,
        'dalle_prompt': dalle_prompt,
        'generated_url': generated_url,
        'fetched_photos': [{'number': p['number'], 'url': p['url']} for p in fetched_photos],
        'used_product_photo': bool(product_image_bytes),
        'used_current_cover': bool(current_cover_bytes),
    }
