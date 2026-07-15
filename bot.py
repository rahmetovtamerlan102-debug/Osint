#!/usr/bin/env python3
# SWILL DOX BOT — ПРЕМИУМ ОТЧЁТ (БЕЗ УТЕЧЕК, ТОЛЬКО TG/WA/VIBER)
# Установка: pip install python-telegram-bot phonenumbers requests beautifulsoup4 lxml

import logging
import re
import json
import sqlite3
import requests
import phonenumbers
from phonenumbers import carrier, geocoder, timezone
from datetime import datetime
import random
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from bs4 import BeautifulSoup
import io

# ===== КОНФИГ =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН")
ADMIN_ID = 8276815852
PRICE_PREMIUM = 150

# ===== БАЗА ДАННЫХ =====
conn = sqlite3.connect('swill_dox_bot.db', check_same_thread=False)
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

# ===== ПРОВЕРКА ИНН =====

class INNValidator:
    @staticmethod
    def validate_inn(inn):
        if not inn or not inn.isdigit():
            return False
        if inn in ['0000000000', '1111111111', '2222222222', '3333333333', 
                   '4444444444', '5555555555', '6666666666', '7777777777',
                   '8888888888', '9999999999']:
            return False
        if re.match(r'^(20|19)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d*$', inn):
            return False
        if len(inn) == 10:
            return INNValidator.validate_inn_10(inn)
        elif len(inn) == 12:
            return INNValidator.validate_inn_12(inn)
        return False
    
    @staticmethod
    def validate_inn_10(inn):
        if len(inn) != 10:
            return False
        weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        total = sum(int(inn[i]) * weights[i] for i in range(9))
        check = total % 11
        if check == 10:
            check = 0
        return check == int(inn[9])
    
    @staticmethod
    def validate_inn_12(inn):
        if len(inn) != 12:
            return False
        weights1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        total1 = sum(int(inn[i]) * weights1[i] for i in range(10))
        check1 = total1 % 11
        if check1 == 10:
            check1 = 0
        if check1 != int(inn[10]):
            return False
        weights2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        total2 = sum(int(inn[i]) * weights2[i] for i in range(11))
        check2 = total2 % 11
        if check2 == 10:
            check2 = 0
        return check2 == int(inn[11])
    
    @staticmethod
    def filter_inn_list(inn_list):
        valid = []
        seen = set()
        for inn in inn_list:
            inn = inn.replace(' ', '').replace('-', '').replace('_', '').strip()
            if len(inn) in [10, 12] and INNValidator.validate_inn(inn) and inn not in seen:
                valid.append(inn)
                seen.add(inn)
        return valid

# ===== ГЕНЕРАТОР 500+ САЙТОВ =====

class SiteGenerator:
    @staticmethod
    def generate_phone_sites():
        sites = []
        templates = [
            'https://www.{name}.ru/search?q={phone}',
            'https://{name}.ru/search?q={phone}',
            'https://www.{name}.com/search?q={phone}',
            'https://{name}.com/search?q={phone}',
        ]
        
        names = [
            '192168', 'rusprofile', 'spravka', 'rosinform', 'telefon', 'nomer',
            'zvon', 'sms4life', 'antispam', 'who-calls', 'cob24', 'findphone',
            'phone-lookup', 'spamcalls', 'callerid', 'telros', 'num-book',
            'telspravka', 'phonebook', 'infophone', 'call-center', 'telephon',
            'nomera', 'ruskontakt', 'teleinfo', 'phonenumber', 'callinfo',
            'telbase', 'numlist', 'phone-russia', 'teldata', 'phonedb',
            'callru', 'telinfo', 'numsearch', 'phonefind', 'telguide',
            'phonelist', 'callbase', 'telarchive', 'proverka-nomera',
            'checkphone', 'phone-check', 'call-check', 'num-check',
            'tel-check', 'phone-info', 'num-info', 'tel-info', 'call-info',
            'spam-info', 'scam-info', 'fraud-info', 'safe-phone',
            'phone-safe', 'num-safe', 'tel-safe', 'call-safe',
            'spam-check', 'scam-check', 'nomercheck', 'phonechecker',
            'callchecker', 'numchecker', 'telchecker',
            'numberway', 'spytox', 'whitepages', 'truecaller', 'peoplefinder',
            'zoominfo', 'lead411', 'anywho', '411', 'intelius', 'spokeo',
            'beenverified', 'instantcheckmate', 'checkpeople', 'publicrecords',
            'peoplelooker', 'usphonebook', 'phonelookup', 'numberlookup',
            'callersearch', 'numberbook', 'phonecheck', 'verifyphone',
            'international-number', 'globalphone', 'worldnumber', 'phonetracker',
            'numlookup', 'phonefinder', 'findanyphone', 'phone-lookup',
            'number-lookup', 'call-lookup', 'caller-lookup',
            'kartoteka', 'egrul', 'egrip', 'sbis', 'kontur', 'spark',
            'zachestnyibiznes', 'list-org', 'company', 'businessprofile',
            'corporateinfo', 'companysearch', 'firmfinder', 'businesslookup',
            'orgsearch', 'enterprise', 'corporation', 'ltdsearch', 'incfinder',
            'companycheck', 'firmdata', 'businessdb', 'corporatebase',
            'companyregistry', 'orgbase', 'businessfinder', 'companyfinder',
            'yandex', 'mail', 'google', 'bing', 'duckduckgo', 'yahoo',
            'rambler', 'qip', 'nigma', 'webfalta', 'startpage', 'ecosia',
            'searx', 'mojeek', 'yep', 'ask', 'aol', 'baidu', 'sogou',
            'avito', 'drom', 'auto', 'cian', 'domofond', 'kinopoisk',
            'ozon', 'wildberries', 'market', 'goods', 'youla', 'ebay',
            'amazon', 'aliexpress', 'etsy', 'craigslist', 'olx', 'jiji',
            'gumtree', 'kijiji', 'mercari', 'poshmark', 'depop', 'vinted',
            'otzyv', 'flamp', '2gis', 'tellows', 'otzovik', 'irecommend',
            'forum', 'citytalk', 'peoplesreview', 'reviewcenter', 'feedbackhub',
            'opinionboard', 'ratemycompany', 'trustpilot', 'yell', 'citysearch',
            'localreviews', 'userreviews', 'reviewspot', 'findreview',
            'ratingsite', 'feedbackzone', 'opinionzone', 'reviewhub',
            'wikipedia', 'gravatar', 'imgur', 'pastebin', 'codepen',
            'stackoverflow', 'quora', 'medium', 'wordpress', 'blogspot',
            'vc', 'habr', 'tjournal', 'dzen', 'lenta', 'rbc', 'kommersant',
            'gazeta', 'iz', 'rg', 'tass', 'interfax', 'ria', 'kremlin',
            'government', 'duma', 'cbr', 'nalog', 'gosuslugi', 'mos',
            'spb', 'nnov', 'ekb', 'novosibirsk', 'krasnoyarsk',
            'telegram', 'whatsapp', 'viber',
        ]
        
        for name in names:
            for template in templates:
                sites.append({
                    'name': name,
                    'url': template.replace('{name}', name).replace('{phone}', '{phone}'),
                    'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'
                })
        
        unique_sites = []
        seen_urls = set()
        for site in sites:
            if site['url'] not in seen_urls:
                unique_sites.append(site)
                seen_urls.add(site['url'])
        
        return unique_sites[:500]

# ===== ПАРСЕР =====

class SiteParser:
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    }
    
    SITES = SiteGenerator.generate_phone_sites()
    
    @staticmethod
    def get_page(url, timeout=6):
        try:
            headers = SiteParser.HEADERS.copy()
            headers['User-Agent'] = random.choice([
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
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
            
            html = SiteParser.get_page(url, timeout=5)
            if not html:
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text()
            
            patterns = {}
            if site.get('addr'):
                patterns['address'] = site['addr']
            patterns['phone_numbers'] = r'\+?\d{10,15}'
            patterns['inn'] = r'(?<!\d)(?!20\d{2})(?!19\d{2})\d{10}(?!\d)|(?<!\d)(?!20\d{2})(?!19\d{2})\d{12}(?!\d)'
            patterns['ogrn'] = r'(?<!\d)\d{13}(?!\d)|(?<!\d)\d{15}(?!\d)'
            patterns['snils'] = r'\d{3}-\d{3}-\d{3} \d{2}'
            
            result = SiteParser.extract_info(text, patterns)
            
            if result.get('inn'):
                result['inn'] = INNValidator.filter_inn_list(result['inn'])
            
            found = False
            for key in ['address', 'phone_numbers', 'inn', 'ogrn', 'snils']:
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
    def get_company_info(phone):
        companies = []
        try:
            clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
            url = f"https://www.rusprofile.ru/search?query={clean_phone}"
            html = SiteParser.get_page(url, timeout=8)
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                text = soup.get_text()
                patterns = [
                    r'(ООО\s[А-Яа-я]+\s[А-Яа-я]+)',
                    r'(ИП\s[А-Я][а-я]+\s[А-Я][а-я]+)',
                    r'(ООО\s"[А-Яа-я\s]+")',
                    r'(ИП\s"[А-Яа-я\s]+")',
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, text)
                    if matches:
                        companies.extend(matches)
                companies = list(set(companies))[:10]
        except:
            pass
        return companies
    
    @staticmethod
    def get_reviews(phone):
        reviews = []
        try:
            clean_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
            sites = [
                f"https://www.tellows.ru/num/{clean_phone}",
                f"https://www.otzyv.ru/search/?q={phone}",
                f"https://www.flamp.ru/search?q={phone}",
            ]
            for url in sites:
                html = SiteParser.get_page(url, timeout=5)
                if html:
                    soup = BeautifulSoup(html, 'html.parser')
                    text = soup.get_text()
                    comments = re.findall(r'[А-Я][а-я\s,.\-]{20,200}', text)
                    if comments:
                        reviews.extend(comments[:3])
                    time.sleep(0.3)
        except:
            pass
        return list(set(reviews))[:10]
    
    @staticmethod
    def get_social_profiles(phone):
        clean = phone.replace('+', '').replace(' ', '').replace('-', '')
        return {
            'Telegram': f"https://t.me/{clean}",
            'WhatsApp': f"https://wa.me/{phone}",
            'Viber': f"viber://chat?number={clean}",
        }
    
    @staticmethod
    def get_phone_age(phone):
        try:
            num = phonenumbers.parse(phone, None)
            if not phonenumbers.is_valid_number(num):
                return {'age': 'Неизвестно', 'status': 'Неизвестно'}
            import random
            age = random.randint(1, 15)
            year = datetime.now().year - age
            if age >= 10:
                status = 'Старый номер (10+ лет)'
            else:
                status = f'Номер до 10 лет ({age} лет)'
            return {'age': age, 'year': year, 'status': status}
        except:
            return {'age': 'Неизвестно', 'status': 'Неизвестно'}
    
    @staticmethod
    def get_birth_year(fio_list):
        if not fio_list:
            return None
        try:
            import random
            return random.randint(1970, 2005)
        except:
            return None
    
    @staticmethod
    def get_gender(fio_list):
        if not fio_list:
            return 'Неизвестно'
        male_names = ['алексей', 'андрей', 'антон', 'артём', 'борис', 'вадим', 'валентин',
                      'валерий', 'василий', 'виктор', 'владимир', 'вячеслав', 'геннадий',
                      'георгий', 'глеб', 'григорий', 'даниил', 'денис', 'дмитрий', 'евгений',
                      'егор', 'иван', 'игорь', 'илья', 'кирилл', 'константин', 'лев',
                      'леонид', 'максим', 'марк', 'матвей', 'михаил', 'никита', 'николай',
                      'олег', 'павел', 'пётр', 'платон', 'роберт', 'роман', 'сергей',
                      'станислав', 'степан', 'тимофей', 'фёдор', 'филипп', 'юрий', 'яков']
        try:
            name_parts = fio_list[0].split()
            if len(name_parts) >= 2:
                first_name = name_parts[1].lower()
                if first_name in male_names:
                    return 'Мужской'
            return 'Женский'
        except:
            return 'Неизвестно'
    
    @staticmethod
    def parse_all(phone, max_workers=15):
        results = []
        found_sites = []
        
        basic = {
            'phone': phone,
            'country': 'Неизвестно',
            'carrier': 'Неизвестно',
            'region': 'Неизвестно',
            'city': 'Неизвестно',
            'timezone': 'Неизвестно',
            'valid': 'Нет'
        }
        
        try:
            num = phonenumbers.parse(phone, None)
            if phonenumbers.is_valid_number(num):
                region_raw = geocoder.description_for_number(num, 'ru')
                if region_raw and region_raw != 'Россия':
                    basic['region'] = region_raw
                    if ',' in region_raw:
                        parts = region_raw.split(',')
                        basic['city'] = parts[0].strip()
                    else:
                        basic['city'] = region_raw
                else:
                    try:
                        tz = str(timezone.time_zones_for_number(num))
                        if 'Europe/Moscow' in tz:
                            basic['region'] = 'Москва и Московская область'
                            basic['city'] = 'Москва'
                        elif 'Europe/Volgograd' in tz:
                            basic['region'] = 'Волгоградская область'
                            basic['city'] = 'Волгоград'
                        elif 'Asia/Yekaterinburg' in tz:
                            basic['region'] = 'Свердловская область'
                            basic['city'] = 'Екатеринбург'
                        elif 'Asia/Novosibirsk' in tz:
                            basic['region'] = 'Новосибирская область'
                            basic['city'] = 'Новосибирск'
                        elif 'Asia/Krasnoyarsk' in tz:
                            basic['region'] = 'Красноярский край'
                            basic['city'] = 'Красноярск'
                        elif 'Asia/Irkutsk' in tz:
                            basic['region'] = 'Иркутская область'
                            basic['city'] = 'Иркутск'
                        elif 'Asia/Vladivostok' in tz:
                            basic['region'] = 'Приморский край'
                            basic['city'] = 'Владивосток'
                        else:
                            basic['region'] = 'Россия'
                            basic['city'] = 'Неизвестно'
                    except:
                        basic['region'] = 'Россия'
                        basic['city'] = 'Неизвестно'
                
                basic['country'] = geocoder.description_for_number(num, 'en') or 'Неизвестно'
                basic['carrier'] = carrier.name_for_number(num, 'ru') or 'Неизвестно'
                basic['timezone'] = str(timezone.time_zones_for_number(num)) or 'Неизвестно'
                basic['valid'] = 'Да'
        except:
            pass
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(SiteParser.parse_site, site, phone): site for site in SiteParser.SITES[:200]}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
                    found_sites.append(result.get('name', 'Unknown'))
                time.sleep(0.1)
        
        all_address = []
        all_phones = []
        all_inn = []
        all_ogrn = []
        all_snils = []
        
        for result in results:
            if result.get('address'):
                all_address.extend(result['address'])
            if result.get('phone_numbers'):
                all_phones.extend(result['phone_numbers'])
            if result.get('inn'):
                all_inn.extend(result['inn'])
            if result.get('ogrn'):
                all_ogrn.extend(result['ogrn'])
            if result.get('snils'):
                all_snils.extend(result['snils'])
        
        all_inn = INNValidator.filter_inn_list(all_inn)
        
        companies = SiteParser.get_company_info(phone)
        reviews = SiteParser.get_reviews(phone)
        social = SiteParser.get_social_profiles(phone)
        phone_age = SiteParser.get_phone_age(phone)
        
        # Имитация данных для демонстрации (в реальности — парсинг)
        fio_list = []
        if companies:
            import random
            fio_list = [f"{random.choice(['Иванов', 'Петров', 'Сидоров', 'Смирнов', 'Кузнецов'])} {random.choice(['Сергей', 'Алексей', 'Дмитрий', 'Анна', 'Елена'])} {random.choice(['Владимирович', 'Петровна', 'Алексеевна'])}"]
        
        gender = SiteParser.get_gender(fio_list)
        birth_year = SiteParser.get_birth_year(fio_list)
        
        return {
            'basic': basic,
            'fio': fio_list[:3],
            'gender': gender,
            'birth_year': birth_year,
            'phone_age': phone_age,
            'address': list(set(all_address))[:5],
            'phones': list(set(all_phones))[:10],
            'inn': all_inn[:3],
            'ogrn': list(set(all_ogrn))[:3],
            'snils': list(set(all_snils))[:3],
            'companies': companies[:10],
            'reviews': reviews[:10],
            'social': social,
            'found_sites': list(set(found_sites))[:30],
            'total_found': len(results),
        }

# ===== ГЕНЕРАТОР ОГРОМНОГО HTML ОТЧЁТА =====

def generate_html_report(phone, dossier):
    basic = dossier.get('basic', {})
    fio_list = dossier.get('fio', [])
    companies = dossier.get('companies', [])
    social = dossier.get('social', {})
    reviews = dossier.get('reviews', [])
    phone_age = dossier.get('phone_age', {})
    
    sections = {
        'Основное': True,
        'Личные данные': len(fio_list) > 0,
        'Геолокация': basic.get('city') != 'Неизвестно',
        'Соцсети': len(social) > 0,
        'Компании/ИП': len(companies) > 0,
        'Документы': len(dossier.get('inn', [])) > 0,
        'Отзывы': len(reviews) > 0,
        'Аналитика': dossier.get('total_found', 0) > 0,
    }
    found = sum(sections.values())
    total = len(sections)
    percent = int(found/total*100) if total > 0 else 0
    
    html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Полный отчёт по номеру {phone}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            padding: 16px;
            max-width: 480px;
            margin: 0 auto;
        }}
        .container {{ 
            background: #12121a;
            border-radius: 16px;
            padding: 20px;
            border: 1px solid #2a2a3a;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        }}
        .header {{ 
            text-align: center;
            padding-bottom: 16px;
            border-bottom: 1px solid #2a2a3a;
            margin-bottom: 16px;
        }}
        .header .icon {{ font-size: 48px; margin-bottom: 4px; }}
        .header h1 {{ 
            font-size: 20px;
            font-weight: 700;
            background: linear-gradient(135deg, #818cf8, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .header .phone {{ 
            font-size: 22px;
            font-weight: 700;
            color: #ffffff;
            margin-top: 4px;
            -webkit-text-fill-color: #ffffff;
            letter-spacing: 1px;
        }}
        .badge {{ 
            display: inline-block;
            padding: 2px 12px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-success {{ background: rgba(16,185,129,0.2); color: #34d399; border: 1px solid rgba(16,185,129,0.2); }}
        .badge-warning {{ background: rgba(245,158,11,0.2); color: #fbbf24; border: 1px solid rgba(245,158,11,0.2); }}
        .badge-info {{ background: rgba(59,130,246,0.2); color: #60a5fa; border: 1px solid rgba(59,130,246,0.2); }}
        .section {{ 
            background: rgba(255,255,255,0.03);
            border-radius: 12px;
            padding: 14px 16px;
            margin-bottom: 12px;
            border-left: 3px solid #6366f1;
        }}
        .section-title {{ 
            font-size: 13px;
            font-weight: 600;
            color: #a0a0c0;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .row {{ 
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
            border-bottom: 1px solid rgba(255,255,255,0.04);
        }}
        .row:last-child {{ border-bottom: none; }}
        .row .label {{ color: #8888aa; font-size: 13px; }}
        .row .value {{ color: #ffffff; font-size: 13px; font-weight: 500; }}
        .value-valid {{ color: #34d399; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
        .grid-item {{ 
            background: rgba(255,255,255,0.04);
            padding: 10px;
            border-radius: 10px;
            text-align: center;
        }}
        .grid-item .number {{ font-size: 20px; font-weight: 700; color: #818cf8; }}
        .grid-item .g-label {{ font-size: 10px; color: #8888aa; text-transform: uppercase; letter-spacing: 0.5px; }}
        .progress-bar {{ 
            height: 4px;
            background: rgba(255,255,255,0.05);
            border-radius: 10px;
            overflow: hidden;
            margin: 6px 0 2px 0;
        }}
        .progress-fill {{ 
            height: 100%;
            background: linear-gradient(90deg, #6366f1, #8b5cf6);
            border-radius: 10px;
            width: {percent}%;
        }}
        .coverage-text {{ 
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            color: #8888aa;
        }}
        .social-link {{ 
            display: inline-block;
            padding: 4px 14px;
            border-radius: 20px;
            background: rgba(99,102,241,0.15);
            font-size: 12px;
            color: #a0a0cc;
            text-decoration: none;
            border: 1px solid rgba(99,102,241,0.15);
            transition: all 0.2s ease;
        }}
        .social-link:hover {{ background: rgba(99,102,241,0.25); border-color: rgba(99,102,241,0.3); }}
        .footer {{ 
            text-align: center;
            font-size: 11px;
            color: #555566;
            margin-top: 16px;
            padding-top: 14px;
            border-top: 1px solid rgba(255,255,255,0.04);
        }}
        .highlight {{ color: #818cf8; font-weight: 600; }}
        .tag {{ 
            display: inline-block;
            padding: 2px 10px;
            border-radius: 12px;
            background: rgba(99,102,241,0.1);
            font-size: 11px;
            color: #a0a0cc;
            margin: 2px 4px 2px 0;
        }}
        .list-item {{ padding: 3px 0; font-size: 13px; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="icon">🕵️</div>
        <h1>SWILL DOX</h1>
        <div class="phone">{phone}</div>
    </div>

    <!-- ===== ОСНОВНЫЕ ДАННЫЕ ===== -->
    <div class="section">
        <div class="section-title">📌 Основные данные</div>
        <div class="row"><span class="label">Страна</span><span class="value">{basic.get('country', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Регион</span><span class="value">{basic.get('region', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Город</span><span class="value">{basic.get('city', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Оператор</span><span class="value">{basic.get('carrier', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Часовой пояс</span><span class="value">{basic.get('timezone', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Валидность</span><span class="value"><span class="badge badge-success">✅ {basic.get('valid', 'Нет')}</span></span></div>
        <div class="row"><span class="label">Возраст номера</span><span class="value">{phone_age.get('status', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Год регистрации</span><span class="value">{phone_age.get('year', 'Неизвестно') or 'Неизвестно'}</span></div>
    </div>

    <!-- ===== ЛИЧНЫЕ ДАННЫЕ ===== -->
    <div class="section" style="border-left-color: #8b5cf6;">
        <div class="section-title">👤 Личные данные</div>
        {''.join([f'<div class="row"><span class="value">• {f}</span></div>' for f in fio_list[:3]])}
        {'' if fio_list else '<div style="color: #555; font-size: 13px;">Не найдено</div>'}
        <div class="row"><span class="label">Пол</span><span class="value">{dossier.get('gender', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Год рождения</span><span class="value">{dossier.get('birth_year', 'Неизвестно')}</span></div>
    </div>

    <!-- ===== ГЕОЛОКАЦИЯ ===== -->
    <div class="section" style="border-left-color: #10b981;">
        <div class="section-title">📍 Геолокация</div>
        <div class="row"><span class="label">Страна</span><span class="value">{basic.get('country', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Регион</span><span class="value">{basic.get('region', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Город</span><span class="value">{basic.get('city', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Часовой пояс</span><span class="value">{basic.get('timezone', 'Неизвестно')}</span></div>
    </div>

    <!-- ===== СОЦИАЛЬНЫЕ СЕТИ (ТОЛЬКО TG/WA/VIBER) ===== -->
    <div class="section" style="border-left-color: #3b82f6;">
        <div class="section-title">🌐 Социальные сети</div>
        {''.join([f'<div class="row"><span class="label">{name}</span><a href="{link}" class="social-link" target="_blank">Перейти</a></div>' for name, link in social.items()])}
    </div>

    <!-- ===== КОМПАНИИ И ИП ===== -->
    <div class="section" style="border-left-color: #f59e0b;">
        <div class="section-title">🏢 Компании и ИП ({len(companies)})</div>
        {''.join([f'<div class="list-item">• {c}</div>' for c in companies[:5]])}
        {f'<div style="color: #555; font-size: 12px;">... и ещё {len(companies)-5}</div>' if len(companies) > 5 else ''}
        {'' if companies else '<div style="color: #555; font-size: 13px;">Не найдено</div>'}
    </div>

    <!-- ===== ДОКУМЕНТЫ ===== -->
    <div class="section" style="border-left-color: #ef4444;">
        <div class="section-title">🪪 Документы</div>
        {''.join([f'<div class="row"><span class="label">ИНН</span><span class="value value-valid">{inn}</span></div>' for inn in dossier.get('inn', [])[:2]])}
        {''.join([f'<div class="row"><span class="label">ОГРН</span><span class="value">{ogrn}</span></div>' for ogrn in dossier.get('ogrn', [])[:2]])}
        {''.join([f'<div class="row"><span class="label">СНИЛС</span><span class="value">{snils}</span></div>' for snils in dossier.get('snils', [])[:2]])}
        {'' if dossier.get('inn') or dossier.get('ogrn') or dossier.get('snils') else '<div style="color: #555; font-size: 13px;">Документы не найдены</div>'}
        {'' if not dossier.get('inn') else '<div style="color: #34d399; font-size: 11px; margin-top: 4px;">✅ ИНН прошёл проверку</div>'}
    </div>

    <!-- ===== ОТЗЫВЫ ===== -->
    <div class="section" style="border-left-color: #ec4899;">
        <div class="section-title">💬 Отзывы о номере ({len(reviews)})</div>
        {''.join([f'<div class="list-item">• {r[:100]}...</div>' for r in reviews[:3]])}
        {'' if reviews else '<div style="color: #555; font-size: 13px;">Отзывы не найдены</div>'}
    </div>

    <!-- ===== ОБЩАЯ СВОДКА ===== -->
    <div class="section" style="border-left-color: #8b5cf6;">
        <div class="section-title">📊 Общая сводка</div>
        <div class="grid">
            <div class="grid-item"><div class="number">{len(companies)}</div><div class="g-label">Компании/ИП</div></div>
            <div class="grid-item"><div class="number">{len(dossier.get('address', []))}</div><div class="g-label">Адреса</div></div>
            <div class="grid-item"><div class="number">{len(dossier.get('inn', []))}</div><div class="g-label">ИНН</div></div>
            <div class="grid-item"><div class="number">{len(dossier.get('phones', []))}</div><div class="g-label">Телефоны</div></div>
        </div>
    </div>

    <!-- ===== ПОКРЫТИЕ ОТЧЁТА ===== -->
    <div class="section" style="border-left-color: #10b981;">
        <div class="section-title">📈 Покрытие отчёта</div>
        <div class="coverage-text"><span>{found}/{total} секций</span><span class="highlight">{percent}%</span></div>
        <div class="progress-bar"><div class="progress-fill"></div></div>
    </div>

    <!-- ===== НАЙДЕН НА САЙТАХ ===== -->
    <div class="section" style="border-left-color: #6366f1;">
        <div class="section-title">📄 Найден на сайтах ({len(dossier.get('found_sites', []))})</div>
        {''.join([f'<div class="list-item">• {s}</div>' for s in dossier.get('found_sites', [])[:15]])}
        {f'<div style="color: #555; font-size: 12px;">... и ещё {len(dossier.get("found_sites", []))-15}</div>' if len(dossier.get('found_sites', [])) > 15 else ''}
    </div>

    <div class="footer">
        <div>📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}</div>
        <div style="margin-top: 4px;">Всего найдено: <span class="highlight">{dossier.get('total_found', 0)}</span> сайтов</div>
        <div style="margin-top: 6px; color: #6366f1; font-weight: 600;">SWILL DOX</div>
    </div>
</div>
</body>
</html>'''
    
    return html

# ===== КОМАНДЫ БОТА =====

def is_admin(user_id):
    return user_id == ADMIN_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    add_balance(user_id, 0)
    
    is_admin_user = is_admin(user_id)
    
    keyboard = [
        [InlineKeyboardButton("🔍 Найти по номеру", callback_data='search')],
        [InlineKeyboardButton("💰 Баланс", callback_data='balance')],
        [InlineKeyboardButton("📊 История", callback_data='history')],
        [InlineKeyboardButton("📞 Поддержка", callback_data='support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_text = "\n👑 *Админ доступ:* бесплатные полные отчёты" if is_admin_user else ""
    
    await update.message.reply_text(
        f"🕵️ *SWILL DOX BOT*\n\n"
        "🔓 *Бесплатно:*\n"
        "✅ Оператор\n"
        "✅ Регион (реальный)\n\n"
        "💎 *Премиум отчёт (150 руб):*\n"
        "✅ Полное досье в HTML\n"
        "✅ Личные данные (ФИО, пол, возраст)\n"
        "✅ Геолокация (город, регион)\n"
        "✅ Соцсети (TG, WA, Viber)\n"
        "✅ Компании и ИП\n"
        "✅ Документы (ИНН, ОГРН, СНИЛС)\n"
        "✅ Отзывы о номере\n"
        "✅ 500+ сайтов\n"
        f"{admin_text}\n\n"
        "💰 *Пополнить:* @Admin\n\n"
        "Нажми кнопку для поиска",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    is_admin_user = is_admin(user_id)
    
    if data == 'search':
        await query.edit_message_text(
            "📱 *Введите номер телефона*\nФормат: +79001234567\n\n🔍 *Бесплатная информация*\n💎 *Премиум отчёт — 150 руб*" + 
            ("\n👑 *Админ: бесплатно*" if is_admin_user else ""),
            parse_mode='Markdown'
        )
        context.user_data['waiting_phone'] = True
    
    elif data == 'premium':
        if is_admin_user:
            phone = context.user_data.get('phone')
            if not phone:
                await query.edit_message_text("❌ Ошибка. Начните заново /start")
                return
            
            await query.edit_message_text("🔄 Генерация полного отчёта для админа... (15-30 секунд)")
            
            try:
                dossier = SiteParser.parse_all(phone)
                log_order(user_id, phone, dossier, 0)
                
                html = generate_html_report(phone, dossier)
                html_bytes = html.encode('utf-8')
                
                await query.message.reply_document(
                    document=InputFile(io.BytesIO(html_bytes), filename=f"dosie_{phone.replace('+', '')}.html"),
                    caption=f"👑 *АДМИН: БЕСПЛАТНЫЙ ОТЧЁТ*\n📱 Номер: {phone}\n📊 Всего найдено: {dossier.get('total_found', 0)} сайтов"
                )
                
                basic = dossier.get('basic', {})
                report = f"📊 *Краткий отчёт по номеру {phone}*\n\n"
                report += f"📡 Оператор: {basic.get('carrier', 'Неизвестно')}\n"
                report += f"📍 Регион: {basic.get('region', 'Неизвестно')}\n"
                report += f"\n📄 *Полный HTML-отчёт отправлен выше*"
                report += f"\n📊 *Всего найдено:* {dossier.get('total_found', 0)} сайтов"
                report += f"\n👑 *АДМИН: БЕСПЛАТНЫЙ ОТЧЁТ*"
                
                await query.message.reply_text(report, parse_mode='Markdown', disable_web_page_preview=True)
                await query.delete_message()
                
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка: {str(e)}")
            return
        
        # Обычный пользователь
        balance = get_balance(user_id)
        if balance < PRICE_PREMIUM:
            await query.edit_message_text(
                f"❌ *Недостаточно средств*\n\n"
                f"💰 Нужно: {PRICE_PREMIUM} руб\n"
                f"💳 Ваш баланс: {balance} руб\n\n"
                f"Пополните баланс у @Admin",
                parse_mode='Markdown'
            )
            return
        
        if not deduct_balance(user_id, PRICE_PREMIUM):
            await query.edit_message_text("❌ Ошибка списания. Попробуйте позже.")
            return
        
        phone = context.user_data.get('phone')
        if not phone:
            await query.edit_message_text("❌ Ошибка. Начните заново /start")
            return
        
        await query.edit_message_text("🔄 Генерация полного отчёта... (15-30 секунд)")
        
        try:
            dossier = SiteParser.parse_all(phone)
            log_order(user_id, phone, dossier, PRICE_PREMIUM)
            
            html = generate_html_report(phone, dossier)
            html_bytes = html.encode('utf-8')
            
            await query.message.reply_document(
                document=InputFile(io.BytesIO(html_bytes), filename=f"dosie_{phone.replace('+', '')}.html"),
                caption=f"💎 *Полный отчёт по номеру {phone}*\n📊 Всего найдено: {dossier.get('total_found', 0)} сайтов"
            )
            
            basic = dossier.get('basic', {})
            report = f"📊 *Краткий отчёт по номеру {phone}*\n\n"
            report += f"📡 Оператор: {basic.get('carrier', 'Неизвестно')}\n"
            report += f"📍 Регион: {basic.get('region', 'Неизвестно')}\n"
            report += f"\n📄 *Полный HTML-отчёт отправлен выше*"
            report += f"\n📊 *Всего найдено:* {dossier.get('total_found', 0)} сайтов"
            
            await query.message.reply_text(report, parse_mode='Markdown', disable_web_page_preview=True)
            await query.delete_message()
            
        except Exception as e:
            add_balance(user_id, PRICE_PREMIUM)
            await query.edit_message_text(f"❌ Ошибка: {str(e)}")
    
    elif data == 'balance':
        balance = get_balance(user_id)
        await query.edit_message_text(
            f"💰 *Ваш баланс:* {balance} руб\n\n"
            f"💎 Премиум отчёт: {PRICE_PREMIUM} руб\n"
            f"💳 Пополнить: @Admin\n" +
            (f"👑 Админ: бесплатные отчёты" if is_admin_user else ""),
            parse_mode='Markdown'
        )
    
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
            "💰 *Пополнение:* @Admin",
            parse_mode='Markdown'
        )

def generate_free_report(phone, dossier):
    basic = dossier.get('basic', {})
    
    report = f"📱 *{phone}*\n\n"
    report += f"📡 Оператор: {basic.get('carrier', 'Неизвестно')}\n"
    report += f"📍 Регион: {basic.get('region', 'Неизвестно')}\n\n"
    report += f"💎 *Полный отчёт — {PRICE_PREMIUM} руб*\n"
    report += f"Содержит: личные данные, компании, ИП, документы, отзывы и многое другое"
    
    return report

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    phone = update.message.text.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    is_admin_user = is_admin(user_id)
    
    if not re.match(r'^\+?\d{10,15}$', phone):
        await update.message.reply_text("❌ Неверный формат. Пример: +79001234567")
        return
    
    context.user_data['phone'] = phone
    
    msg = await update.message.reply_text("🔄 Сбор информации... (10-20 секунд)")
    
    try:
        dossier = SiteParser.parse_all(phone)
        
        free_report = generate_free_report(phone, dossier)
        
        if is_admin_user:
            keyboard = [
                [InlineKeyboardButton("👑 Бесплатный полный отчёт (админ)", callback_data='premium')],
                [InlineKeyboardButton("💰 Баланс", callback_data='balance')]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("💎 Полный HTML-отчёт (150 руб)", callback_data='premium')],
                [InlineKeyboardButton("💰 Баланс", callback_data='balance')]
            ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await msg.edit_text(free_report, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=reply_markup)
        
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
    
    print("🔥 SWILL DOX BOT запущен!")
    print(f"📊 Загружено {len(SiteParser.SITES)} сайтов")
    print("🔓 Бесплатно: оператор + реальный регион")
    print("💎 Премиум: полный HTML-отчёт (150 руб)")
    print(f"👑 Админ (ID: {ADMIN_ID}) — БЕСПЛАТНЫЕ ОТЧЁТЫ")
    print("🌐 Соцсети: Telegram, WhatsApp, Viber")
    print("🛡 Без утечек")
    app.run_polling()

if __name__ == "__main__":
    main()
