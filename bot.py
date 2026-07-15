#!/usr/bin/env python3
# SWILL 50 SITES DOX BOT — RENDER EDITION
# Установка: pip install python-telegram-bot phonenumbers requests beautifulsoup4 lxml python-anticaptcha

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
from anticaptchaofficial.recaptchav2proxyless import *

# ===== КОНФИГ ДЛЯ RENDER =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН")  # Берем из переменных окружения
ADMIN_ID = 8276815852  # Твой ID
PRICE_PER_REPORT = 0  # БЕСПЛАТНО (0 рублей)

# Anti-Captcha ключ (бесплатно до 20 капч/день)
ANTICAPTCHA_KEY = os.environ.get("ANTICAPTCHA_KEY", "ВАШ_КЛЮЧ")  # Получить на anticaptcha.com

# ===== БАЗА ДАННЫХ (SQLite) =====
conn = sqlite3.connect('swill_50_sites.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, reg_date TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS orders 
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, phone TEXT, result TEXT, price INTEGER, date TEXT)''')
conn.commit()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== ОБХОД КАПЧИ =====

class CaptchaSolver:
    """Решение капчи через AntiCaptcha"""
    
    @staticmethod
    def solve_recaptcha(site_url, site_key):
        """Решение ReCaptcha v2"""
        try:
            solver = recaptchaV2Proxyless()
            solver.set_verbose(1)
            solver.set_key(ANTICAPTCHA_KEY)
            solver.set_website_url(site_url)
            solver.set_website_key(site_key)
            
            g_response = solver.solve_and_return_solution()
            if g_response:
                return g_response
            else:
                logger.error(f"Не удалось решить капчу: {solver.err_string}")
                return None
        except Exception as e:
            logger.error(f"Ошибка капчи: {e}")
            return None
    
    @staticmethod
    def detect_captcha(html):
        """Обнаружение капчи на странице"""
        if 'g-recaptcha' in html or 'recaptcha' in html:
            return True
        return False

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

def deduct_balance(user_id, amount):
    balance = get_balance(user_id)
    if balance >= amount:
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
        conn.commit()
        return True
    return False

def log_order(user_id, phone, result, price):
    cursor.execute("INSERT INTO orders (user_id, phone, result, price, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, phone, json.dumps(result, ensure_ascii=False), price, datetime.now().isoformat()))
    conn.commit()

# ===== 50 САЙТОВ С ПАТТЕРНАМИ =====

class SiteParser:
    """Парсинг 50 сайтов с обходом капчи"""
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
        'Connection': 'keep-alive',
    }
    
    # ===== 50 САЙТОВ =====
    SITES = [
        # ===== 1-20: РОССИЙСКИЕ СПРАВОЧНИКИ =====
        {
            'name': '192168.ru',
            'url': 'https://www.192168.ru/search.php?query={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+',
            'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'
        },
        {
            'name': 'spravka.arkhangelsk.ru',
            'url': 'https://spravka.arkhangelsk.ru/phone/{phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'rosinform.ru',
            'url': 'https://www.rosinform.ru/phone/?number={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'rusprofile.ru',
            'url': 'https://www.rusprofile.ru/search?query={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+',
            'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'
        },
        {
            'name': 'telefon.guru',
            'url': 'https://www.telefon.guru/number/{phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'nomer.org',
            'url': 'https://www.nomer.org/?search={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+',
            'addr': r'[А-Яа-я]+\s+ул\.\s+[А-Яа-я]+\s+д\.\s+\d+'
        },
        {
            'name': 'zvon.ru',
            'url': 'https://www.zvon.ru/number/{phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'sms4life.ru',
            'url': 'https://sms4life.ru/search/?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'antispam.ru',
            'url': 'https://antispam.ru/search?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'who-calls.ru',
            'url': 'https://who-calls.ru/number/{phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'kinopoisk.ru',
            'url': 'https://www.kinopoisk.ru/search/?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'avito.ru',
            'url': 'https://www.avito.ru/search?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+',
            'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'
        },
        {
            'name': 'drom.ru',
            'url': 'https://www.drom.ru/search/?text={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'auto.ru',
            'url': 'https://auto.ru/search/?text={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'cian.ru',
            'url': 'https://www.cian.ru/search/?query={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+',
            'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'
        },
        {
            'name': 'domofond.ru',
            'url': 'https://www.domofond.ru/search?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+',
            'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'
        },
        {
            'name': 'yandex.ru',
            'url': 'https://yandex.ru/search/?text={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'mail.ru',
            'url': 'https://mail.ru/search?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'ok.ru',
            'url': 'https://ok.ru/search?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'vk.com',
            'url': 'https://vk.com/search?c[section]=people&c[q]={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        
        # ===== 21-30: ЗАРУБЕЖНЫЕ СПРАВОЧНИКИ =====
        {
            'name': 'numberway.com',
            'url': 'https://www.numberway.com/phone/{phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'spytox.com',
            'url': 'https://www.spytox.com/phone/{phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'whitepages.com',
            'url': 'https://www.whitepages.com/phone/{phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+',
            'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'
        },
        {
            'name': 'truecaller.com',
            'url': 'https://www.truecaller.com/search?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'peoplefinder.com',
            'url': 'https://www.peoplefinder.com/search/{phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+',
            'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'
        },
        {
            'name': 'zoominfo.com',
            'url': 'https://www.zoominfo.com/search?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'lead411.com',
            'url': 'https://www.lead411.com/search?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'anywho.com',
            'url': 'https://www.anywho.com/phone/{phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+',
            'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'
        },
        {
            'name': '411.com',
            'url': 'https://www.411.com/phone/{phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+',
            'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'
        },
        {
            'name': 'intelius.com',
            'url': 'https://www.intelius.com/search?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        
        # ===== 31-35: БАЗЫ УТЕЧЕК =====
        {
            'name': 'leakcheck.net',
            'url': 'https://leakcheck.net/search?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'breachdirectory.org',
            'url': 'https://www.breachdirectory.org/search.php?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'scamalytics.com',
            'url': 'https://scamalytics.com/phone/{phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'haveibeenpwned.com',
            'url': 'https://haveibeenpwned.com/account/{email}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'dehashed.com',
            'url': 'https://dehashed.com/search?query={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        
        # ===== 36-45: СОЦИАЛЬНЫЕ СЕТИ =====
        {
            'name': 'telegram',
            'url': 'https://t.me/{phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'whatsapp',
            'url': 'https://wa.me/{phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'instagram.com',
            'url': 'https://www.instagram.com/explore/search/keyword/?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'tiktok.com',
            'url': 'https://www.tiktok.com/search?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'youtube.com',
            'url': 'https://www.youtube.com/results?search_query={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'twitter.com',
            'url': 'https://twitter.com/search?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'facebook.com',
            'url': 'https://www.facebook.com/search/top?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'github.com',
            'url': 'https://github.com/search?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'linkedin.com',
            'url': 'https://www.linkedin.com/search/results/all/?keywords={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        {
            'name': 'reddit.com',
            'url': 'https://www.reddit.com/search/?q={phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'
        },
        
        # ===== 46-50: ФОРУМЫ И ОТЗЫВЫ =====
        {
            'name': 'otzyv.ru',
            'url': 'https://www.otzyv.ru/search/?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': 'flamp.ru',
            'url': 'https://www.flamp.ru/search?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'
        },
        {
            'name': '2gis.ru',
            'url': 'https://www.2gis.ru/search?q={phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+',
            'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'
        },
        {
            'name': 'google.com/maps',
            'url': 'https://www.google.com/maps/search/{phone}',
            'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+',
            'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'
        },
        {
            'name': 'yandex.ru/maps',
            'url': 'https://yandex.ru/maps/search/{phone}',
            'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+',
            'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'
        }
    ]
    
    @staticmethod
    def get_page_with_captcha(url, timeout=15):
        """Получение страницы с обходом капчи"""
        try:
            # Первый запрос
            headers = SiteParser.HEADERS.copy()
            headers['User-Agent'] = random.choice([
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            ])
            r = requests.get(url, headers=headers, timeout=timeout)
            r.encoding = 'utf-8'
            html = r.text
            
            # Проверка на капчу
            if CaptchaSolver.detect_captcha(html):
                logger.info(f"Обнаружена капча на {url}, решаем...")
                
                # Пытаемся найти site-key
                site_key_match = re.search(r'data-sitekey="([^"]+)"', html)
                if not site_key_match:
                    site_key_match = re.search(r'sitekey="([^"]+)"', html)
                if not site_key_match:
                    site_key_match = re.search(r'recaptcha.*?key=([^&\s"]+)', html)
                
                if site_key_match:
                    site_key = site_key_match.group(1)
                    captcha_token = CaptchaSolver.solve_recaptcha(url, site_key)
                    if captcha_token:
                        # Повторный запрос с токеном
                        headers['Content-Type'] = 'application/x-www-form-urlencoded'
                        data = {'g-recaptcha-response': captcha_token}
                        r2 = requests.post(url, headers=headers, data=data, timeout=timeout)
                        if r2.status_code == 200:
                            return r2.text
            
            return html if r.status_code == 200 else None
            
        except Exception as e:
            logger.error(f"Ошибка запроса {url}: {e}")
            return None
    
    @staticmethod
    def extract_info(text, patterns):
        """Извлечение информации из текста"""
        result = {}
        for key, pattern in patterns.items():
            if pattern:
                matches = re.findall(pattern, text)
                if matches:
                    result[key] = list(set(matches))
        return result
    
    @staticmethod
    def parse_site(site, phone, email=None):
        """Парсинг одного сайта с обходом капчи"""
        try:
            url = site['url']
            clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
            url = url.replace('{phone}', clean_phone)
            if email:
                url = url.replace('{email}', email)
            
            html = SiteParser.get_page_with_captcha(url, timeout=15)
            if not html:
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text()
            
            # Извлечение данных
            patterns = {}
            if site.get('fio'):
                patterns['fio'] = site['fio']
            if site.get('addr'):
                patterns['address'] = site['addr']
            
            patterns['phone_numbers'] = r'\+?\d{10,15}'
            patterns['emails'] = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            patterns['inn'] = r'\d{10,12}'
            patterns['snils'] = r'\d{3}-\d{3}-\d{3} \d{2}'
            patterns['passport'] = r'\d{4} \d{6}'
            
            result = SiteParser.extract_info(text, patterns)
            
            found = False
            for key in ['fio', 'address', 'phone_numbers', 'emails', 'inn', 'snils', 'passport']:
                if result.get(key):
                    found = True
                    break
            
            if found:
                result['name'] = site['name']
                result['url'] = url
                return result
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка парсинга {site['name']}: {e}")
            return None
    
    @staticmethod
    def parse_all(phone, email=None, max_workers=5):
        """Парсинг всех 50 сайтов с многопоточностью"""
        results = []
        found_sites = []
        
        # Основная информация
        basic = {
            'phone': phone,
            'country': 'Неизвестно',
            'carrier': 'Неизвестно',
            'timezone': 'Неизвестно',
            'valid': 'Нет'
        }
        
        try:
            num = phonenumbers.parse(phone, None)
            if phonenumbers.is_valid_number(num):
                basic['country'] = geocoder.description_for_number(num, 'ru') or 'Неизвестно'
                basic['carrier'] = carrier.name_for_number(num, 'ru') or 'Неизвестно'
                basic['timezone'] = str(timezone.time_zones_for_number(num)) or 'Неизвестно'
                basic['valid'] = 'Да'
        except:
            pass
        
        # Многопоточный парсинг (меньше потоков чтобы не заблокировали)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(SiteParser.parse_site, site, phone, email): site for site in SiteParser.SITES}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
                    found_sites.append(result.get('name', 'Unknown'))
                time.sleep(0.5)  # Задержка между запросами
        
        # Сбор всех найденных данных
        all_fio = []
        all_address = []
        all_phones = []
        all_emails = []
        all_inn = []
        all_snils = []
        all_passport = []
        
        for result in results:
            if result.get('fio'):
                all_fio.extend(result['fio'])
            if result.get('address'):
                all_address.extend(result['address'])
            if result.get('phone_numbers'):
                all_phones.extend(result['phone_numbers'])
            if result.get('emails'):
                all_emails.extend(result['emails'])
            if result.get('inn'):
                all_inn.extend(result['inn'])
            if result.get('snils'):
                all_snils.extend(result['snils'])
            if result.get('passport'):
                all_passport.extend(result['passport'])
        
        return {
            'basic': basic,
            'fio': list(set(all_fio))[:5],
            'address': list(set(all_address))[:3],
            'phones': list(set(all_phones))[:5],
            'emails': list(set(all_emails))[:5],
            'inn': list(set(all_inn))[:2],
            'snils': list(set(all_snils))[:2],
            'passport': list(set(all_passport))[:2],
            'found_sites': list(set(found_sites))[:20],
            'total_found': len(results),
            'details': results[:10]
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
        "🕵️ *SWILL 50 SITES DOX (FREE)*\n\n"
        "Бесплатный парсинг 50 сайтов по номеру телефона:\n"
        "✅ 20 российских справочников\n"
        "✅ 10 зарубежных справочников\n"
        "✅ 5 баз утечек\n"
        "✅ 10 социальных сетей\n"
        "✅ 5 форумов и отзывов\n\n"
        "Собирает: ФИО, адрес, ИНН, СНИЛС, паспорт, email\n\n"
        "🔥 *Стоимость: БЕСПЛАТНО*\n"
        "🔄 *Обход капчи:* включен\n\n"
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
            "📢 *Канал:* @SwillChannel\n"
            "🌐 *VPN:* https://t.me/Swillnet_bot",
            parse_mode='Markdown'
        )

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    phone = update.message.text.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    
    if not re.match(r'^\+?\d{10,15}$', phone):
        await update.message.reply_text("❌ Неверный формат. Пример: +79001234567")
        return
    
    # БЕСПЛАТНО - без проверки баланса
    msg = await update.message.reply_text("🔄 Парсинг 50 сайтов с обходом капчи... (может занять 30-60 секунд)")
    
    try:
        # Парсинг всех сайтов
        dossier = SiteParser.parse_all(phone)
        
        # Сохранение
        log_order(user_id, phone, dossier, 0)  # 0 рублей
        
        # Формирование отчёта
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
        
        if dossier.get('inn'):
            report += f"🔢 *ИНН:* {', '.join(dossier['inn'])}\n"
        if dossier.get('snils'):
            report += f"🆔 *СНИЛС:* {', '.join(dossier['snils'])}\n"
        if dossier.get('passport'):
            report += f"🪪 *Паспорт:* {', '.join(dossier['passport'])}\n"
        
        if dossier.get('found_sites'):
            report += f"\n📄 *Найден на сайтах:*\n"
            for site in dossier['found_sites'][:10]:
                report += f"• {site}\n"
        
        report += f"\n*Всего найдено:* {dossier.get('total_found', 0)} сайтов"
        report += f"\n*Дата:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        report += f"\n\n💡 *Бесплатный запрос*"
        
        await msg.edit_text(report, parse_mode='Markdown', disable_web_page_preview=True)
        
        if user_id == ADMIN_ID:
            await update.message.reply_document(
                document=json.dumps(dossier, ensure_ascii=False, indent=2),
                filename=f"dossier_{phone}.json",
                caption="📄 Полный JSON-отчёт"
            )
        
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_phone'):
        await handle_phone(update, context)
        context.user_data['waiting_phone'] = False

# ===== ЗАПУСК =====

def main():
    # Для Render используем webhook или polling
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🔥 SWILL 50 SITES DOX (FREE) запущен!")
    print(f"📊 Загружено {len(SiteParser.SITES)} сайтов для парсинга")
    print(f"👤 Админ: {ADMIN_ID}")
    print("🔄 Обход капчи: включен")
    print("💰 Цена: БЕСПЛАТНО")
    print("🌐 https://t.me/Swillnet_bot")
    
    # Для Render используем polling
    app.run_polling()

if __name__ == "__main__":
    main()
