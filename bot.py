#!/usr/bin/env python3
# SWILL 50 SITES DOX BOT — 50 САЙТОВ + ФИЛЬТР ФИО
# Установка: pip install python-telegram-bot phonenumbers requests beautifulsoup4 lxml

import logging
import re
import json
import sqlite3
import requests
import phonenumbers
from phonenumbers import carrier, geocoder, timezone
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from bs4 import BeautifulSoup
import time
import random
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===== КОНФИГ =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН")
ADMIN_ID = 8276815852
PRICE_PER_REPORT = 0

# ===== БАЗА ДАННЫХ =====
conn = sqlite3.connect('swill_50_sites.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, reg_date TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS orders 
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, phone TEXT, result TEXT, price INTEGER, date TEXT)''')
conn.commit()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== ФУНКЦИИ БАЗЫ =====
def get_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    return result[0] if result else 0

def add_balance(user_id, amount):
    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance, reg_date) VALUES (?, ?, ?)", 
                   (user_id, 0, datetime.now().isoformat()))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def log_order(user_id, phone, result, price):
    cursor.execute("INSERT INTO orders (user_id, phone, result, price, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, phone, json.dumps(result, ensure_ascii=False), price, datetime.now().isoformat()))
    conn.commit()

# ===== ФИЛЬТР ФИО =====

class FIOFilter:
    """Фильтр реальных ФИО"""
    
    STOP_WORDS = [
        'администрация', 'губернатор', 'министр', 'департамент',
        'управление', 'комитет', 'совет', 'служба', 'агентство',
        'инспекция', 'название', 'категория', 'язык', 'bahasa',
        'malaysia', 'indonesia', 'english', 'russian', 'пользователь',
        'аккаунт', 'профиль', 'страница', 'запись', 'комментарий',
        'президент', 'директор', 'руководитель', 'специалист',
        'консультант', 'менеджер', 'эксперт', 'аналитик', 'инженер',
        'технолог', 'конструктор', 'проект', 'система', 'программа'
    ]
    
    @staticmethod
    def is_valid_name(name):
        if len(name) < 2 or len(name) > 25:
            return False
        if not re.match(r'^[А-ЯЁ][а-яё]+$', name):
            return False
        name_lower = name.lower()
        for stop in FIOFilter.STOP_WORDS:
            if stop in name_lower:
                return False
        return True
    
    @staticmethod
    def validate_fio(fio):
        if not fio:
            return False
        parts = fio.strip().split()
        if len(parts) < 2:
            return False
        if not FIOFilter.is_valid_name(parts[0]):
            return False
        if not FIOFilter.is_valid_name(parts[1]):
            return False
        fio_lower = fio.lower()
        forbidden = ['президент', 'директор', 'руководитель', 'специалист', 'консультант', 'менеджер']
        for word in forbidden:
            if word in fio_lower:
                return False
        return True
    
    @staticmethod
    def filter_fio_list(fio_list):
        valid = []
        for fio in fio_list:
            if FIOFilter.validate_fio(fio) and fio not in valid:
                valid.append(fio)
        return valid

# ===== 50 САЙТОВ =====

class SiteParser:
    """Парсинг 50 сайтов"""
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
        'Connection': 'keep-alive',
    }
    
    SITES = [
        # ===== 1-10: РОССИЙСКИЕ СПРАВОЧНИКИ =====
        {'name': '192168.ru', 'url': 'https://www.192168.ru/search.php?query={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'},
        {'name': 'spravka.arkhangelsk.ru', 'url': 'https://spravka.arkhangelsk.ru/phone/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'rosinform.ru', 'url': 'https://www.rosinform.ru/phone/?number={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'rusprofile.ru', 'url': 'https://www.rusprofile.ru/search?query={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'},
        {'name': 'telefon.guru', 'url': 'https://www.telefon.guru/number/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'nomer.org', 'url': 'https://www.nomer.org/?search={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+\s+ул\.\s+[А-Яа-я]+\s+д\.\s+\d+'},
        {'name': 'zvon.ru', 'url': 'https://www.zvon.ru/number/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'sms4life.ru', 'url': 'https://sms4life.ru/search/?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'antispam.ru', 'url': 'https://antispam.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'who-calls.ru', 'url': 'https://who-calls.ru/number/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        
        # ===== 11-20: ПОИСКОВЫЕ СИСТЕМЫ И МАРКЕТПЛЕЙСЫ =====
        {'name': 'yandex.ru', 'url': 'https://yandex.ru/search/?text={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'mail.ru', 'url': 'https://mail.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'avito.ru', 'url': 'https://www.avito.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'drom.ru', 'url': 'https://www.drom.ru/search/?text={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'auto.ru', 'url': 'https://auto.ru/search/?text={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'cian.ru', 'url': 'https://www.cian.ru/search/?query={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'domofond.ru', 'url': 'https://www.domofond.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'kinopoisk.ru', 'url': 'https://www.kinopoisk.ru/search/?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'ok.ru', 'url': 'https://ok.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'vk.com', 'url': 'https://vk.com/search?c[section]=people&c[q]={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        
        # ===== 21-30: ЗАРУБЕЖНЫЕ СПРАВОЧНИКИ =====
        {'name': 'numberway.com', 'url': 'https://www.numberway.com/phone/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'spytox.com', 'url': 'https://www.spytox.com/phone/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'whitepages.com', 'url': 'https://www.whitepages.com/phone/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'truecaller.com', 'url': 'https://www.truecaller.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'peoplefinder.com', 'url': 'https://www.peoplefinder.com/search/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'zoominfo.com', 'url': 'https://www.zoominfo.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'lead411.com', 'url': 'https://www.lead411.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'anywho.com', 'url': 'https://www.anywho.com/phone/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': '411.com', 'url': 'https://www.411.com/phone/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'intelius.com', 'url': 'https://www.intelius.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        
        # ===== 31-35: БАЗЫ УТЕЧЕК =====
        {'name': 'leakcheck.net', 'url': 'https://leakcheck.net/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'breachdirectory.org', 'url': 'https://www.breachdirectory.org/search.php?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'scamalytics.com', 'url': 'https://scamalytics.com/phone/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'haveibeenpwned.com', 'url': 'https://haveibeenpwned.com/account/{email}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'dehashed.com', 'url': 'https://dehashed.com/search?query={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        
        # ===== 36-45: СОЦИАЛЬНЫЕ СЕТИ =====
        {'name': 'telegram', 'url': 'https://t.me/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'whatsapp', 'url': 'https://wa.me/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'instagram.com', 'url': 'https://www.instagram.com/explore/search/keyword/?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'tiktok.com', 'url': 'https://www.tiktok.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'youtube.com', 'url': 'https://www.youtube.com/results?search_query={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'twitter.com', 'url': 'https://twitter.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'facebook.com', 'url': 'https://www.facebook.com/search/top?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'github.com', 'url': 'https://github.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'linkedin.com', 'url': 'https://www.linkedin.com/search/results/all/?keywords={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'reddit.com', 'url': 'https://www.reddit.com/search/?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        
        # ===== 46-50: ФОРУМЫ И ОТЗЫВЫ =====
        {'name': 'otzyv.ru', 'url': 'https://www.otzyv.ru/search/?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'flamp.ru', 'url': 'https://www.flamp.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': '2gis.ru', 'url': 'https://www.2gis.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'google.com/maps', 'url': 'https://www.google.com/maps/search/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'yandex.ru/maps', 'url': 'https://yandex.ru/maps/search/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'}
    ]
    
    @staticmethod
    def get_page(url, timeout=15):
        try:
            headers = SiteParser.HEADERS.copy()
            headers['User-Agent'] = random.choice([
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            ])
            r = requests.get(url, headers=headers, timeout=timeout)
            r.encoding = 'utf-8'
            return r.text if r.status_code == 200 else None
        except:
            return None
    
    @staticmethod
    def extract_info(text, patterns):
        result = {}
        for key, pattern in patterns.items():
            if pattern:
                matches = re.findall(pattern, text)
                if matches:
                    result[key] = list(set(matches))
        return result
    
    @staticmethod
    def parse_site(site, phone):
        try:
            url = site['url']
            clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
            url = url.replace('{phone}', clean_phone)
            
            html = SiteParser.get_page(url, timeout=10)
            if not html:
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text()
            
            patterns = {}
            if site.get('fio'):
                patterns['fio'] = site['fio']
            if site.get('addr'):
                patterns['address'] = site['addr']
            patterns['phone_numbers'] = r'\+?\d{10,15}'
            patterns['emails'] = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            
            result = SiteParser.extract_info(text, patterns)
            
            if result.get('fio'):
                result['fio'] = FIOFilter.filter_fio_list(result['fio'])
            
            found = False
            for key in ['fio', 'address', 'phone_numbers', 'emails']:
                if result.get(key):
                    found = True
                    break
            
            if found:
                result['name'] = site['name']
                result['url'] = url
                return result
            return None
        except:
            return None
    
    @staticmethod
    def parse_all(phone, max_workers=3):
        results = []
        found_sites = []
        
        basic = {
            'phone': phone,
            'country': 'Неизвестно',
            'carrier': 'Неизвестно',
            'valid': 'Нет'
        }
        
        try:
            num = phonenumbers.parse(phone, None)
            if phonenumbers.is_valid_number(num):
                basic['country'] = geocoder.description_for_number(num, 'ru') or 'Неизвестно'
                basic['carrier'] = carrier.name_for_number(num, 'ru') or 'Неизвестно'
                basic['valid'] = 'Да'
        except:
            pass
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(SiteParser.parse_site, site, phone): site for site in SiteParser.SITES}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
                    found_sites.append(result.get('name', 'Unknown'))
                time.sleep(0.3)
        
        all_fio = []
        all_address = []
        all_phones = []
        all_emails = []
        
        for result in results:
            if result.get('fio'):
                all_fio.extend(result['fio'])
            if result.get('address'):
                all_address.extend(result['address'])
            if result.get('phone_numbers'):
                all_phones.extend(result['phone_numbers'])
            if result.get('emails'):
                all_emails.extend(result['emails'])
        
        all_fio = FIOFilter.filter_fio_list(all_fio)
        
        return {
            'basic': basic,
            'fio': all_fio[:5],
            'address': list(set(all_address))[:3],
            'phones': list(set(all_phones))[:5],
            'emails': list(set(all_emails))[:5],
            'found_sites': list(set(found_sites))[:20],
            'total_found': len(results)
        }

# ===== КОМАНДЫ БОТА =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    add_balance(user_id, 0)
    
    keyboard = [
        [InlineKeyboardButton("🔍 Найти по номеру", callback_data='search')],
        [InlineKeyboardButton("📊 История", callback_data='history')],
        [InlineKeyboardButton("📞 Поддержка", callback_data='support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🕵️ *SWILL 50 SITES DOX*\n\n"
        "Бесплатный парсинг 50 сайтов по номеру:\n"
        "✅ 10 российских справочников\n"
        "✅ 10 поисковых систем\n"
        "✅ 10 зарубежных справочников\n"
        "✅ 5 баз утечек\n"
        "✅ 10 социальных сетей\n"
        "✅ 5 форумов и отзывов\n\n"
        "Собирает: ФИО, адрес, email\n\n"
        "🔥 *Стоимость: БЕСПЛАТНО*\n\n"
        "Нажми кнопку для поиска",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if data == 'search':
        await query.edit_message_text(
            "📱 *Введите номер телефона*\nФормат: +79001234567",
            parse_mode='Markdown'
        )
        context.user_data['waiting_phone'] = True
    
    elif data == 'history':
        cursor.execute("SELECT phone, price, date FROM orders WHERE user_id=? ORDER BY date DESC LIMIT 10", (user_id,))
        orders = cursor.fetchall()
        if orders:
            text = "📊 *Последние запросы:*\n\n"
            for phone, price, date in orders:
                text += f"📱 {phone} — {price} руб ({date[:10]})\n"
            await query.edit_message_text(text, parse_mode='Markdown')
        else:
            await query.edit_message_text("📊 История пуста", parse_mode='Markdown')
    
    elif data == 'support':
        await query.edit_message_text(
            "📞 *Поддержка:* @SwillSupport\n"
            "📢 *Канал:* @SwillChannel",
            parse_mode='Markdown'
        )

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    phone = update.message.text.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    
    if not re.match(r'^\+?\d{10,15}$', phone):
        await update.message.reply_text("❌ Неверный формат. Пример: +79001234567")
        return
    
    msg = await update.message.reply_text("🔄 Парсинг 50 сайтов... (20-60 секунд)")
    
    try:
        dossier = SiteParser.parse_all(phone)
        log_order(user_id, phone, dossier, 0)
        
        report = f"🕵️ *ДОСЬЕ ПО НОМЕРУ {phone}*\n\n"
        
        basic = dossier.get('basic', {})
        report += f"📌 *Основное:*\n"
        report += f"🌍 Страна: {basic.get('country', 'Неизвестно')}\n"
        report += f"📡 Оператор: {basic.get('carrier', 'Неизвестно')}\n"
        report += f"✅ Валидность: {basic.get('valid', 'Нет')}\n\n"
        
        if dossier.get('fio'):
            report += f"👤 *ФИО:*\n"
            for fio in dossier['fio']:
                report += f"• {fio}\n"
            report += "\n"
        
        if dossier.get('address'):
            report += f"📍 *Адреса:*\n"
            for addr in dossier['address']:
                report += f"• {addr}\n"
            report += "\n"
        
        if dossier.get('emails'):
            report += f"📧 *Email:*\n"
            for email in dossier['emails']:
                report += f"• {email}\n"
            report += "\n"
        
        if dossier.get('found_sites'):
            report += f"📄 *Найден на сайтах:*\n"
            for site in dossier['found_sites'][:10]:
                report += f"• {site}\n"
        
        report += f"\n*Всего найдено:* {dossier.get('total_found', 0)} сайтов"
        report += f"\n*Дата:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        
        await msg.edit_text(report, parse_mode='Markdown', disable_web_page_preview=True)
        
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_phone'):
        await handle_phone(update, context)
        context.user_data['waiting_phone'] = False

# ===== ЗАПУСК =====

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🔥 SWILL 50 SITES DOX запущен!")
    print(f"📊 Загружено {len(SiteParser.SITES)} сайтов")
    print("🛡 Фильтр ФИО: ВКЛЮЧЕН")
    app.run_polling()

if __name__ == "__main__":
    main()
