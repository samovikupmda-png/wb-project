from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from modules.database import db, Settings, Product, ABTest, ABVariant
from modules import ab_test as ab_module
import os
import subprocess

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.secret_key = 'wb-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'wb_tool.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db.init_app(app)

with app.app_context():
    os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'static', 'uploads'), exist_ok=True)
    db.create_all()
    from sqlalchemy import text
    with db.engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE ab_test ADD COLUMN campaign_type VARCHAR(32) DEFAULT "manual"'))
            conn.commit()
        except Exception:
            pass
    if not Settings.query.first():
        db.session.add(Settings())
        db.session.commit()


# ── Главная ──────────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    products_count = Product.query.count()
    active_tests = ABTest.query.filter_by(status='active').count()
    completed_tests = ABTest.query.filter_by(status='completed').count()
    recent_tests = ABTest.query.order_by(ABTest.created_at.desc()).limit(5).all()
    return render_template('dashboard.html',
                           products_count=products_count,
                           active_tests=active_tests,
                           completed_tests=completed_tests,
                           recent_tests=recent_tests)


# ── Товары ───────────────────────────────────────────────────────────────────

@app.route('/products')
def products():
    all_products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('products.html', products=all_products)


@app.route('/products/add', methods=['POST'])
def add_product():
    wb_article = request.form.get('wb_article', '').strip()
    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()

    if not wb_article or not name:
        flash('Заполните артикул и название товара', 'error')
        return redirect(url_for('products'))

    if Product.query.filter_by(wb_article=wb_article).first():
        flash('Товар с таким артикулом уже добавлен', 'error')
        return redirect(url_for('products'))

    product = Product(wb_article=wb_article, name=name, category=category)
    db.session.add(product)
    db.session.commit()
    flash(f'Товар «{name}» добавлен', 'success')
    return redirect(url_for('products'))


@app.route('/products/delete/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash('Товар удалён', 'success')
    return redirect(url_for('products'))


# ── AB-тестирование ───────────────────────────────────────────────────────────

@app.route('/ab-tests')
def ab_tests():
    tests = ab_module.get_all_tests()
    products = Product.query.all()
    return render_template('ab_tests.html', tests=tests, products=products)


@app.route('/ab-tests/create', methods=['POST'])
def create_ab_test():
    product_id = request.form.get('product_id')
    name = request.form.get('name', '').strip()
    target_ctr = request.form.get('target_ctr', 3.0)
    min_impressions = request.form.get('min_impressions', 2000)
    rotation_interval = request.form.get('rotation_interval', 30)

    if not product_id or not name:
        flash('Выберите товар и укажите название теста', 'error')
        return redirect(url_for('ab_tests'))

    campaign_type = request.form.get('campaign_type', 'manual')
    test = ab_module.create_test(product_id, name, target_ctr, min_impressions, rotation_interval, campaign_type)
    flash(f'Тест «{name}» создан', 'success')
    return redirect(url_for('ab_test_detail', test_id=test.id))


@app.route('/ab-tests/<int:test_id>')
def ab_test_detail(test_id):
    stats = ab_module.get_test_stats(test_id)
    test = ab_module.get_test(test_id)
    return render_template('ab_test_detail.html', test=test, stats=stats)


@app.route('/ab-tests/<int:test_id>/add-variant', methods=['POST'])
def add_variant(test_id):
    variant_name = request.form.get('variant_name', '').strip()
    image_file = request.files.get('image_file')

    if not variant_name:
        flash('Укажите название варианта', 'error')
        return redirect(url_for('ab_test_detail', test_id=test_id))

    ab_module.add_variant(test_id, variant_name, image_file)
    flash(f'Вариант «{variant_name}» добавлен', 'success')
    return redirect(url_for('ab_test_detail', test_id=test_id))


@app.route('/ab-tests/<int:test_id>/update-stats', methods=['POST'])
def update_stats(test_id):
    data = request.get_json()
    variant_id = data.get('variant_id')
    impressions = data.get('impressions', 0)
    clicks = data.get('clicks', 0)
    ab_module.update_variant_stats(variant_id, impressions, clicks)
    stats = ab_module.get_test_stats(test_id)
    return jsonify(stats)


@app.route('/ab-tests/<int:test_id>/pause', methods=['POST'])
def pause_test(test_id):
    test = ABTest.query.get_or_404(test_id)
    test.status = 'paused' if test.status == 'active' else 'active'
    db.session.commit()
    action = 'приостановлен' if test.status == 'paused' else 'возобновлён'
    flash(f'Тест {action}', 'success')
    return redirect(url_for('ab_test_detail', test_id=test_id))


@app.route('/ab-tests/<int:test_id>/delete', methods=['POST'])
def delete_test(test_id):
    test = ABTest.query.get_or_404(test_id)
    db.session.delete(test)
    db.session.commit()
    flash('Тест удалён', 'success')
    return redirect(url_for('ab_tests'))


# ── Аналитика ─────────────────────────────────────────────────────────────────

@app.route('/analytics')
def analytics():
    return render_template('analytics.html')


# ── Реклама ───────────────────────────────────────────────────────────────────

@app.route('/advertising')
def advertising():
    return render_template('advertising.html')


# ── Настройки ─────────────────────────────────────────────────────────────────

@app.route('/settings')
def settings():
    s = Settings.query.first()
    return render_template('settings.html', settings=s)


@app.route('/settings/save', methods=['POST'])
def save_settings():
    s = Settings.query.first()
    s.wb_stats_api_key = request.form.get('wb_stats_api_key', '').strip()
    s.wb_content_api_key = request.form.get('wb_content_api_key', '').strip()
    s.image_ai_api_key = request.form.get('image_ai_api_key', '').strip()
    db.session.commit()
    flash('Настройки сохранены', 'success')
    return redirect(url_for('settings'))


DEPLOY_TOKEN = 'wb-d3pl0y-k3y-2026'

@app.route('/deploy')
def deploy():
    if request.args.get('token') != DEPLOY_TOKEN:
        return 'Unauthorized', 401
    try:
        env = os.environ.copy()
        env['HOME'] = '/var/www'
        out = subprocess.run(
            ['git', '-C', '/var/www/wb-project', 'pull', 'origin',
             'claude/project-management-analysis-jKH5e'],
            capture_output=True, text=True, timeout=60, env=env
        )
        fix = subprocess.run(
            ['chown', '-R', 'www-data:www-data', '/var/www/wb-project'],
            capture_output=True, text=True
        )
        return f'<pre>OK\n{out.stdout}{out.stderr}</pre>', 200
    except Exception as e:
        return f'<pre>Error: {e}</pre>', 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
