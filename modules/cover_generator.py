"""AI-генерация обложек для товаров WB."""
import os
import uuid
import requests
from datetime import datetime
from modules.database import db, CoverGeneration, ABTest, ABVariant, Settings

UPLOAD_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'static', 'uploads')

STYLE_PROMPTS = {
    'product_photo': (
        'Professional e-commerce product photo on pure white background, '
        'studio lighting, high resolution, sharp details, no shadows'
    ),
    'lifestyle': (
        'Lifestyle product photography, natural light, modern interior, '
        'aspirational feel, high-end commercial photography'
    ),
    'minimalist': (
        'Minimalist product shot, clean white background, single item centered, '
        'premium brand feel, elegant composition'
    ),
    'bold': (
        'Bold graphic product image, vibrant colors, eye-catching, '
        'marketplace hero image, strong visual impact'
    ),
}

SIZE_MAP = {
    'openai': '1024x1024',
    'stability': '1024x1024',
}


def build_prompt(product_name: str, category: str, style: str, custom_prompt: str = '') -> str:
    base_style = STYLE_PROMPTS.get(style, STYLE_PROMPTS['product_photo'])
    subject = f'{product_name}'
    if category:
        subject += f', {category} category'
    if custom_prompt:
        return f'{subject}. {custom_prompt}. {base_style}'
    return f'{subject}. {base_style}'


def _generate_openai(prompt: str, api_key: str, model: str, count: int) -> list[str]:
    """Возвращает список URL изображений."""
    import openai
    client = openai.OpenAI(api_key=api_key)
    response = client.images.generate(
        model=model,
        prompt=prompt,
        n=min(count, 4),
        size='1024x1024',
        quality='standard',
        response_format='url',
    )
    return [item.url for item in response.data]


def _generate_stability(prompt: str, api_key: str, count: int) -> list[str]:
    """Stability AI — возвращает URL-like пути после сохранения на диск."""
    urls = []
    for _ in range(min(count, 4)):
        resp = requests.post(
            'https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image',
            headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
            json={
                'text_prompts': [{'text': prompt, 'weight': 1}],
                'cfg_scale': 7,
                'height': 1024,
                'width': 1024,
                'samples': 1,
                'steps': 30,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        for artifact in data.get('artifacts', []):
            import base64
            img_data = base64.b64decode(artifact['base64'])
            filename = f'gen_{uuid.uuid4().hex}.png'
            path = os.path.join(UPLOAD_DIR, filename)
            with open(path, 'wb') as f:
                f.write(img_data)
            urls.append(filename)
    return urls


def _save_remote_image(url: str) -> str:
    """Скачивает изображение по URL, сохраняет локально, возвращает имя файла."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    ext = '.png'
    if 'jpeg' in resp.headers.get('content-type', ''):
        ext = '.jpg'
    filename = f'gen_{uuid.uuid4().hex}{ext}'
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, 'wb') as f:
        f.write(resp.content)
    return filename


def generate_covers(generation_id: int):
    """
    Основная функция генерации. Запускается синхронно или в фоне.
    Обновляет запись CoverGeneration в БД.
    """
    from app import app
    with app.app_context():
        gen = CoverGeneration.query.get(generation_id)
        if not gen:
            return
        settings = Settings.query.first()
        api_key = settings.image_ai_api_key if settings else ''
        provider = gen.ai_provider
        model = gen.ai_model

        try:
            if not api_key:
                raise ValueError('API-ключ для генерации изображений не задан в настройках')

            if provider == 'openai':
                urls = _generate_openai(gen.prompt, api_key, model, count=2)
                filenames = [_save_remote_image(u) for u in urls]
            elif provider == 'stability':
                filenames = _generate_stability(gen.prompt, api_key, count=2)
            else:
                raise ValueError(f'Неизвестный провайдер: {provider}')

            gen.images = filenames
            gen.status = 'done'
        except Exception as e:
            gen.status = 'error'
            gen.error_message = str(e)

        db.session.commit()


def create_ab_test_from_generation(generation_id: int, test_name: str) -> ABTest:
    """Создаёт A/B тест из результатов генерации."""
    gen = CoverGeneration.query.get_or_404(generation_id)
    test = ABTest(
        product_id=gen.product_id,
        name=test_name,
        status='active',
    )
    db.session.add(test)
    db.session.flush()

    for i, filename in enumerate(gen.images):
        variant = ABVariant(
            test_id=test.id,
            name=f'Вариант {i + 1} (AI)',
            image_filename=filename,
        )
        db.session.add(variant)

    gen.ab_test_id = test.id
    db.session.commit()
    return test


def get_all_generations():
    return CoverGeneration.query.order_by(CoverGeneration.created_at.desc()).all()


def get_generation(gen_id: int) -> CoverGeneration:
    return CoverGeneration.query.get_or_404(gen_id)
