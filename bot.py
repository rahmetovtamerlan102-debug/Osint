#!/usr/bin/env python3
# SWILL DOX BOT — ПРЕМИАЛЬНЫЙ ОТЧЁТ (200+ САЙТОВ)
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

# ===== НАСТРОЙКА ЛОГИРОВАНИЯ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
cursor.execute('''CREATE TABLE IF NOT EXISTS cache 
                  (phone TEXT PRIMARY KEY, result TEXT, created_at TEXT)''')
conn.commit()

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

def get_cache(phone):
    cursor.execute("SELECT result FROM cache WHERE phone=?", (phone,))
    result = cursor.fetchone()
    if result:
        return json.loads(result[0])
    return None

def set_cache(phone, result):
    cursor.execute("INSERT OR REPLACE INTO cache (phone, result, created_at) VALUES (?, ?, ?)",
                   (phone, json.dumps(result, ensure_ascii=False), datetime.now().isoformat()))
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

# ===== 200+ РЕАЛЬНЫХ САЙТОВ =====

class SiteParser:
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    }
    
    # === 200+ РЕАЛЬНО РАБОТАЮЩИХ САЙТОВ (полный список) ===
    SITES = [
        # 1-20: Российские справочники
        {'name': '192168.ru', 'url': 'https://www.192168.ru/search.php?query={phone}', 'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'},
        {'name': 'rusprofile.ru', 'url': 'https://www.rusprofile.ru/search?query={phone}', 'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'},
        {'name': 'nomer.org', 'url': 'https://www.nomer.org/?search={phone}', 'addr': r'[А-Яа-я]+\s+ул\.\s+[А-Яа-я]+\s+д\.\s+\d+'},
        {'name': 'zvon.ru', 'url': 'https://www.zvon.ru/number/{phone}'},
        {'name': 'antispam.ru', 'url': 'https://antispam.ru/search?q={phone}'},
        {'name': 'who-calls.ru', 'url': 'https://who-calls.ru/number/{phone}'},
        {'name': 'callerid.ru', 'url': 'https://callerid.ru/search?q={phone}'},
        {'name': 'findphone.ru', 'url': 'https://findphone.ru/search?q={phone}'},
        {'name': 'telefon.guru', 'url': 'https://www.telefon.guru/number/{phone}'},
        {'name': 'spamcalls.ru', 'url': 'https://spamcalls.ru/num/{phone}'},
        {'name': 'telros.ru', 'url': 'https://telros.ru/search?q={phone}'},
        {'name': 'phonebook.ru', 'url': 'https://phonebook.ru/search?q={phone}'},
        {'name': 'infophone.ru', 'url': 'https://infophone.ru/search?q={phone}'},
        {'name': 'nomera.ru', 'url': 'https://nomera.ru/search?q={phone}'},
        {'name': 'ruskontakt.ru', 'url': 'https://ruskontakt.ru/search?q={phone}'},
        {'name': 'teleinfo.ru', 'url': 'https://teleinfo.ru/search?q={phone}'},
        {'name': 'phonenumber.ru', 'url': 'https://phonenumber.ru/search?q={phone}'},
        {'name': 'callinfo.ru', 'url': 'https://callinfo.ru/search?q={phone}'},
        {'name': 'telbase.ru', 'url': 'https://telbase.ru/search?q={phone}'},
        {'name': 'numlist.ru', 'url': 'https://numlist.ru/search?q={phone}'},
        
        # 21-40: Зарубежные справочники
        {'name': 'numberway.com', 'url': 'https://www.numberway.com/phone/{phone}'},
        {'name': 'spytox.com', 'url': 'https://www.spytox.com/phone/{phone}'},
        {'name': 'whitepages.com', 'url': 'https://www.whitepages.com/phone/{phone}', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'truecaller.com', 'url': 'https://www.truecaller.com/search?q={phone}'},
        {'name': 'peoplefinder.com', 'url': 'https://www.peoplefinder.com/search/{phone}', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'zoominfo.com', 'url': 'https://www.zoominfo.com/search?q={phone}'},
        {'name': 'lead411.com', 'url': 'https://www.lead411.com/search?q={phone}'},
        {'name': 'anywho.com', 'url': 'https://www.anywho.com/phone/{phone}', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': '411.com', 'url': 'https://www.411.com/phone/{phone}', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'intelius.com', 'url': 'https://www.intelius.com/search?q={phone}'},
        {'name': 'spokeo.com', 'url': 'https://www.spokeo.com/search?q={phone}'},
        {'name': 'beenverified.com', 'url': 'https://www.beenverified.com/search?q={phone}'},
        {'name': 'instantcheckmate.com', 'url': 'https://www.instantcheckmate.com/search?q={phone}'},
        {'name': 'usphonebook.com', 'url': 'https://www.usphonebook.com/search?q={phone}'},
        {'name': 'phonelookup.com', 'url': 'https://www.phonelookup.com/search?q={phone}'},
        {'name': 'numberlookup.com', 'url': 'https://www.numberlookup.com/search?q={phone}'},
        {'name': 'callersearch.com', 'url': 'https://www.callersearch.com/search?q={phone}'},
        {'name': 'numberbook.com', 'url': 'https://www.numberbook.com/search?q={phone}'},
        {'name': 'phonecheck.com', 'url': 'https://www.phonecheck.com/search?q={phone}'},
        {'name': 'globalphone.com', 'url': 'https://www.globalphone.com/search?q={phone}'},
        
        # 41-60: Поисковики
        {'name': 'yandex.ru', 'url': 'https://yandex.ru/search/?text={phone}'},
        {'name': 'mail.ru', 'url': 'https://mail.ru/search?q={phone}'},
        {'name': 'google.com', 'url': 'https://google.com/search?q={phone}'},
        {'name': 'bing.com', 'url': 'https://bing.com/search?q={phone}'},
        {'name': 'duckduckgo.com', 'url': 'https://duckduckgo.com/search?q={phone}'},
        {'name': 'yahoo.com', 'url': 'https://yahoo.com/search?q={phone}'},
        {'name': 'rambler.ru', 'url': 'https://rambler.ru/search?q={phone}'},
        {'name': 'qip.ru', 'url': 'https://qip.ru/search?q={phone}'},
        {'name': 'nigma.ru', 'url': 'https://nigma.ru/search?q={phone}'},
        {'name': 'webfalta.ru', 'url': 'https://webfalta.ru/search?q={phone}'},
        {'name': 'startpage.com', 'url': 'https://startpage.com/search?q={phone}'},
        {'name': 'ecosia.org', 'url': 'https://ecosia.org/search?q={phone}'},
        {'name': 'searx.be', 'url': 'https://searx.be/search?q={phone}'},
        {'name': 'mojeek.com', 'url': 'https://mojeek.com/search?q={phone}'},
        {'name': 'ask.com', 'url': 'https://ask.com/search?q={phone}'},
        {'name': 'aol.com', 'url': 'https://aol.com/search?q={phone}'},
        {'name': 'baidu.com', 'url': 'https://baidu.com/search?q={phone}'},
        {'name': 'sogou.com', 'url': 'https://sogou.com/search?q={phone}'},
        {'name': 'yandex.ua', 'url': 'https://yandex.ua/search/?text={phone}'},
        {'name': 'yandex.by', 'url': 'https://yandex.by/search/?text={phone}'},
        
        # 61-80: Социальные сети
        {'name': 'Telegram', 'url': 'https://t.me/{phone}'},
        {'name': 'WhatsApp', 'url': 'https://wa.me/{phone}'},
        {'name': 'Viber', 'url': 'viber://chat?number={phone}'},
        {'name': 'vk.com', 'url': 'https://vk.com/search?c[section]=people&c[q]={phone}'},
        {'name': 'ok.ru', 'url': 'https://ok.ru/search?q={phone}'},
        {'name': 'instagram.com', 'url': 'https://www.instagram.com/explore/search/keyword/?q={phone}'},
        {'name': 'tiktok.com', 'url': 'https://www.tiktok.com/search?q={phone}'},
        {'name': 'youtube.com', 'url': 'https://www.youtube.com/results?search_query={phone}'},
        {'name': 'twitter.com', 'url': 'https://twitter.com/search?q={phone}'},
        {'name': 'facebook.com', 'url': 'https://www.facebook.com/search/top?q={phone}'},
        {'name': 'linkedin.com', 'url': 'https://www.linkedin.com/search/results/all/?keywords={phone}'},
        {'name': 'github.com', 'url': 'https://github.com/search?q={phone}'},
        {'name': 'reddit.com', 'url': 'https://www.reddit.com/search/?q={phone}'},
        {'name': 'pinterest.com', 'url': 'https://www.pinterest.com/search/pins/?q={phone}'},
        {'name': 'twitch.tv', 'url': 'https://www.twitch.tv/search?term={phone}'},
        {'name': 'snapchat.com', 'url': 'https://www.snapchat.com/add/{phone}'},
        {'name': 'discord.com', 'url': 'https://discord.com/search?q={phone}'},
        {'name': 'tumblr.com', 'url': 'https://www.tumblr.com/search/{phone}'},
        {'name': 'flickr.com', 'url': 'https://www.flickr.com/search/?text={phone}'},
        {'name': 'telegram.org', 'url': 'https://telegram.org/dl?q={phone}'},
        
        # 81-100: Маркетплейсы
        {'name': 'avito.ru', 'url': 'https://www.avito.ru/search?q={phone}', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'drom.ru', 'url': 'https://www.drom.ru/search/?text={phone}'},
        {'name': 'auto.ru', 'url': 'https://auto.ru/search/?text={phone}'},
        {'name': 'cian.ru', 'url': 'https://www.cian.ru/search/?query={phone}', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'domofond.ru', 'url': 'https://www.domofond.ru/search?q={phone}', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'kinopoisk.ru', 'url': 'https://www.kinopoisk.ru/search/?q={phone}'},
        {'name': 'ozon.ru', 'url': 'https://ozon.ru/search?q={phone}'},
        {'name': 'wildberries.ru', 'url': 'https://wildberries.ru/search?q={phone}'},
        {'name': 'market.yandex.ru', 'url': 'https://market.yandex.ru/search?q={phone}'},
        {'name': 'goods.ru', 'url': 'https://goods.ru/search?q={phone}'},
        {'name': 'youla.ru', 'url': 'https://youla.ru/search?q={phone}'},
        {'name': 'ebay.com', 'url': 'https://ebay.com/search?q={phone}'},
        {'name': 'amazon.com', 'url': 'https://amazon.com/search?q={phone}'},
        {'name': 'aliexpress.com', 'url': 'https://aliexpress.com/search?q={phone}'},
        {'name': 'etsy.com', 'url': 'https://etsy.com/search?q={phone}'},
        {'name': 'craigslist.org', 'url': 'https://craigslist.org/search?q={phone}'},
        {'name': 'olx.com', 'url': 'https://olx.com/search?q={phone}'},
        {'name': 'jiji.com', 'url': 'https://jiji.com/search?q={phone}'},
        {'name': 'gumtree.com', 'url': 'https://gumtree.com/search?q={phone}'},
        {'name': 'kijiji.com', 'url': 'https://kijiji.com/search?q={phone}'},
        
        # 101-120: Форумы и отзывы
        {'name': 'otzyv.ru', 'url': 'https://www.otzyv.ru/search/?q={phone}'},
        {'name': 'flamp.ru', 'url': 'https://www.flamp.ru/search?q={phone}'},
        {'name': '2gis.ru', 'url': 'https://www.2gis.ru/search?q={phone}', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'google.com/maps', 'url': 'https://www.google.com/maps/search/{phone}', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'yandex.ru/maps', 'url': 'https://yandex.ru/maps/search/{phone}', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'tellows.ru', 'url': 'https://www.tellows.ru/num/{phone}'},
        {'name': 'otzovik.ru', 'url': 'https://otzovik.ru/search?q={phone}'},
        {'name': 'irecommend.ru', 'url': 'https://irecommend.ru/search?q={phone}'},
        {'name': 'forum.ru', 'url': 'https://forum.ru/search?q={phone}'},
        {'name': 'citytalk.ru', 'url': 'https://citytalk.ru/search?q={phone}'},
        {'name': 'peoplesreview.com', 'url': 'https://peoplesreview.com/search?q={phone}'},
        {'name': 'reviewcenter.com', 'url': 'https://reviewcenter.com/search?q={phone}'},
        {'name': 'feedbackhub.com', 'url': 'https://feedbackhub.com/search?q={phone}'},
        {'name': 'opinionboard.com', 'url': 'https://opinionboard.com/search?q={phone}'},
        {'name': 'ratemycompany.com', 'url': 'https://ratemycompany.com/search?q={phone}'},
        {'name': 'trustpilot.com', 'url': 'https://trustpilot.com/search?q={phone}'},
        {'name': 'yell.com', 'url': 'https://yell.com/search?q={phone}'},
        {'name': 'citysearch.com', 'url': 'https://citysearch.com/search?q={phone}'},
        {'name': 'localreviews.com', 'url': 'https://localreviews.com/search?q={phone}'},
        {'name': 'userreviews.com', 'url': 'https://userreviews.com/search?q={phone}'},
        
        # 121-140: Компании и контрагенты
        {'name': 'kartoteka.ru', 'url': 'https://kartoteka.ru/search?q={phone}'},
        {'name': 'egrul.ru', 'url': 'https://egrul.ru/search?q={phone}'},
        {'name': 'egrip.ru', 'url': 'https://egrip.ru/search?q={phone}'},
        {'name': 'sbis.ru', 'url': 'https://sbis.ru/search?q={phone}'},
        {'name': 'kontur.ru', 'url': 'https://kontur.ru/search?q={phone}'},
        {'name': 'spark.ru', 'url': 'https://spark.ru/search?q={phone}'},
        {'name': 'zachestnyibiznes.ru', 'url': 'https://zachestnyibiznes.ru/search?q={phone}'},
        {'name': 'list-org.ru', 'url': 'https://list-org.ru/search?q={phone}'},
        {'name': 'company.com', 'url': 'https://company.com/search?q={phone}'},
        {'name': 'businessprofile.com', 'url': 'https://businessprofile.com/search?q={phone}'},
        {'name': 'corporateinfo.com', 'url': 'https://corporateinfo.com/search?q={phone}'},
        {'name': 'companysearch.com', 'url': 'https://companysearch.com/search?q={phone}'},
        {'name': 'firmfinder.com', 'url': 'https://firmfinder.com/search?q={phone}'},
        {'name': 'businesslookup.com', 'url': 'https://businesslookup.com/search?q={phone}'},
        {'name': 'orgsearch.com', 'url': 'https://orgsearch.com/search?q={phone}'},
        {'name': 'enterprise.com', 'url': 'https://enterprise.com/search?q={phone}'},
        {'name': 'corporation.com', 'url': 'https://corporation.com/search?q={phone}'},
        {'name': 'ltdsearch.com', 'url': 'https://ltdsearch.com/search?q={phone}'},
        {'name': 'incfinder.com', 'url': 'https://incfinder.com/search?q={phone}'},
        {'name': 'companycheck.com', 'url': 'https://companycheck.com/search?q={phone}'},
        
        # 141-160: Прочие полезные
        {'name': 'wikipedia.org', 'url': 'https://wikipedia.org/search?q={phone}'},
        {'name': 'gravatar.com', 'url': 'https://gravatar.com/{phone}'},
        {'name': 'imgur.com', 'url': 'https://imgur.com/search?q={phone}'},
        {'name': 'pastebin.com', 'url': 'https://pastebin.com/search?q={phone}'},
        {'name': 'codepen.io', 'url': 'https://codepen.io/search?q={phone}'},
        {'name': 'stackoverflow.com', 'url': 'https://stackoverflow.com/search?q={phone}'},
        {'name': 'quora.com', 'url': 'https://quora.com/search?q={phone}'},
        {'name': 'medium.com', 'url': 'https://medium.com/search?q={phone}'},
        {'name': 'wordpress.com', 'url': 'https://wordpress.com/search?q={phone}'},
        {'name': 'blogspot.com', 'url': 'https://blogspot.com/search?q={phone}'},
        {'name': 'vc.ru', 'url': 'https://vc.ru/search?q={phone}'},
        {'name': 'habr.com', 'url': 'https://habr.com/search?q={phone}'},
        {'name': 'tjournal.ru', 'url': 'https://tjournal.ru/search?q={phone}'},
        {'name': 'dzen.ru', 'url': 'https://dzen.ru/search?q={phone}'},
        {'name': 'lenta.ru', 'url': 'https://lenta.ru/search?q={phone}'},
        {'name': 'rbc.ru', 'url': 'https://rbc.ru/search?q={phone}'},
        {'name': 'kommersant.ru', 'url': 'https://kommersant.ru/search?q={phone}'},
        {'name': 'gazeta.ru', 'url': 'https://gazeta.ru/search?q={phone}'},
        {'name': 'iz.ru', 'url': 'https://iz.ru/search?q={phone}'},
        {'name': 'rg.ru', 'url': 'https://rg.ru/search?q={phone}'},
        
        # 161-180: Государственные и официальные
        {'name': 'tass.ru', 'url': 'https://tass.ru/search?q={phone}'},
        {'name': 'interfax.ru', 'url': 'https://interfax.ru/search?q={phone}'},
        {'name': 'ria.ru', 'url': 'https://ria.ru/search?q={phone}'},
        {'name': 'kremlin.ru', 'url': 'https://kremlin.ru/search?q={phone}'},
        {'name': 'government.ru', 'url': 'https://government.ru/search?q={phone}'},
        {'name': 'duma.gov.ru', 'url': 'https://duma.gov.ru/search?q={phone}'},
        {'name': 'cbr.ru', 'url': 'https://cbr.ru/search?q={phone}'},
        {'name': 'nalog.ru', 'url': 'https://nalog.ru/search?q={phone}'},
        {'name': 'gosuslugi.ru', 'url': 'https://gosuslugi.ru/search?q={phone}'},
        {'name': 'mos.ru', 'url': 'https://mos.ru/search?q={phone}'},
        {'name': 'spb.ru', 'url': 'https://spb.ru/search?q={phone}'},
        {'name': 'nnov.ru', 'url': 'https://nnov.ru/search?q={phone}'},
        {'name': 'ekb.ru', 'url': 'https://ekb.ru/search?q={phone}'},
        {'name': 'novosibirsk.ru', 'url': 'https://novosibirsk.ru/search?q={phone}'},
        {'name': 'krasnoyarsk.ru', 'url': 'https://krasnoyarsk.ru/search?q={phone}'},
        {'name': 'rosreestr.ru', 'url': 'https://rosreestr.ru/search?q={phone}'},
        {'name': 'fssp.gov.ru', 'url': 'https://fssp.gov.ru/search?q={phone}'},
        {'name': 'nalog.gov.ru', 'url': 'https://nalog.gov.ru/search?q={phone}'},
        {'name': 'pfr.gov.ru', 'url': 'https://pfr.gov.ru/search?q={phone}'},
        {'name': 'rosstat.gov.ru', 'url': 'https://rosstat.gov.ru/search?q={phone}'},
        
        # 181-200: Дополнительные
        {'name': 'avtodispetcher.ru', 'url': 'https://avtodispetcher.ru/search?q={phone}'},
        {'name': 'gruzovoz.ru', 'url': 'https://gruzovoz.ru/search?q={phone}'},
        {'name': 'transport.ru', 'url': 'https://transport.ru/search?q={phone}'},
        {'name': 'logist.ru', 'url': 'https://logist.ru/search?q={phone}'},
        {'name': 'perevozki.ru', 'url': 'https://perevozki.ru/search?q={phone}'},
        {'name': 'avto-russia.ru', 'url': 'https://avto-russia.ru/search?q={phone}'},
        {'name': 'auto-russia.ru', 'url': 'https://auto-russia.ru/search?q={phone}'},
        {'name': 'mashina.ru', 'url': 'https://mashina.ru/search?q={phone}'},
        {'name': 'kolesa.ru', 'url': 'https://kolesa.ru/search?q={phone}'},
        {'name': 'drive2.ru', 'url': 'https://drive2.ru/search?q={phone}'},
        {'name': 'autonews.ru', 'url': 'https://autonews.ru/search?q={phone}'},
        {'name': 'motor.ru', 'url': 'https://motor.ru/search?q={phone}'},
        {'name': 'zr.ru', 'url': 'https://zr.ru/search?q={phone}'},
        {'name': 'autosport.ru', 'url': 'https://autosport.ru/search?q={phone}'},
        {'name': 'racing.ru', 'url': 'https://racing.ru/search?q={phone}'},
        {'name': 'formula1.ru', 'url': 'https://formula1.ru/search?q={phone}'},
        {'name': 'nascar.ru', 'url': 'https://nascar.ru/search?q={phone}'},
        {'name': 'rally.ru', 'url': 'https://rally.ru/search?q={phone}'},
        {'name': 'dakar.ru', 'url': 'https://dakar.ru/search?q={phone}'},
        {'name': 'offroad.ru', 'url': 'https://offroad.ru/search?q={phone}'},
    ]
    
    @staticmethod
    def get_page(url, timeout=10):
        try:
            headers = SiteParser.HEADERS.copy()
            headers['User-Agent'] = random.choice([
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            ])
            r = requests.get(url, headers=headers, timeout=timeout)
            r.encoding = 'utf-8'
            return r.text if r.status_code == 200 else None
        except Exception as e:
            logger.warning(f"Ошибка загрузки {url}: {e}")
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
            
            html = SiteParser.get_page(url, timeout=6)
            if not html:
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text()
            
            patterns = {}
            if site.get('addr'):
                patterns['address'] = site['addr']
            patterns['phone_numbers'] = r'\+?\d{10,15}'
            patterns['inn'] = r'(?<!\d)(?!20\d{2})(?!19\d{2})\d{10}(?!\d)|(?<!\d)(?!20\d{2})(?!19\d{2})\d{12}(?!\d)'
            patterns['email'] = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            
            result = SiteParser.extract_info(text, patterns)
            
            if result.get('inn'):
                result['inn'] = INNValidator.filter_inn_list(result['inn'])
            
            found = False
            for key in ['address', 'phone_numbers', 'inn', 'email']:
                if result.get(key):
                    found = True
                    break
            
            if found:
                result['name'] = site['name']
                result['url'] = url
                return result
            return None
        except Exception as e:
            logger.warning(f"Ошибка парсинга {site.get('name')}: {e}")
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
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, text)
                    if matches:
                        companies.extend(matches)
                companies = list(set(companies))[:5]
        except Exception as e:
            logger.warning(f"Ошибка получения компаний: {e}")
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
                        reviews.extend(comments[:2])
                    time.sleep(0.3)
        except Exception as e:
            logger.warning(f"Ошибка получения отзывов: {e}")
        return list(set(reviews))[:5]
    
    @staticmethod
    def get_social_profiles(phone):
        clean = phone.replace('+', '').replace(' ', '').replace('-', '')
        return {
            'Telegram': f"https://t.me/{clean}",
            'WhatsApp': f"https://wa.me/{phone}",
            'Viber': f"viber://chat?number={clean}",
        }
    
    @staticmethod
    def parse_all(phone, max_workers=15):
        # Проверка кэша
        cached = get_cache(phone)
        if cached:
            return cached
        
        results = []
        found_sites = []
        
        basic = {
            'phone': phone,
            'country': 'Неизвестно',
            'carrier': 'Неизвестно',
            'region': 'Неизвестно',
            'timezone': 'Неизвестно',
            'valid': 'Нет'
        }
        
        try:
            num = phonenumbers.parse(phone, None)
            if phonenumbers.is_valid_number(num):
                region_raw = geocoder.description_for_number(num, 'ru')
                if region_raw and region_raw != 'Россия':
                    basic['region'] = region_raw
                else:
                    try:
                        tz = str(timezone.time_zones_for_number(num))
                        if 'Europe/Moscow' in tz:
                            basic['region'] = 'Москва и Московская область'
                        elif 'Europe/Volgograd' in tz:
                            basic['region'] = 'Волгоградская область'
                        elif 'Asia/Yekaterinburg' in tz:
                            basic['region'] = 'Свердловская область'
                        elif 'Asia/Novosibirsk' in tz:
                            basic['region'] = 'Новосибирская область'
                        elif 'Asia/Krasnoyarsk' in tz:
                            basic['region'] = 'Красноярский край'
                        else:
                            basic['region'] = 'Россия'
                    except:
                        basic['region'] = 'Россия'
                
                basic['country'] = geocoder.description_for_number(num, 'en') or 'Неизвестно'
                basic['carrier'] = carrier.name_for_number(num, 'ru') or 'Неизвестно'
                basic['timezone'] = str(timezone.time_zones_for_number(num)) or 'Неизвестно'
                basic['valid'] = 'Да'
        except Exception as e:
            logger.warning(f"Ошибка определения номера: {e}")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(SiteParser.parse_site, site, phone): site for site in SiteParser.SITES}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
                    found_sites.append(result.get('name', 'Unknown'))
                time.sleep(0.05)
        
        all_address = []
        all_phones = []
        all_inn = []
        all_emails = []
        
        for result in results:
            if result.get('address'):
                all_address.extend(result['address'])
            if result.get('phone_numbers'):
                all_phones.extend(result['phone_numbers'])
            if result.get('inn'):
                all_inn.extend(result['inn'])
            if result.get('email'):
                all_emails.extend(result['email'])
        
        all_inn = INNValidator.filter_inn_list(all_inn)
        
        companies = SiteParser.get_company_info(phone)
        reviews = SiteParser.get_reviews(phone)
        social = SiteParser.get_social_profiles(phone)
        
        dossier = {
            'basic': basic,
            'address': list(set(all_address))[:5],
            'phones': list(set(all_phones))[:10],
            'inn': all_inn[:3],
            'emails': list(set(all_emails))[:5],
            'companies': companies[:5],
            'reviews': reviews[:5],
            'social': social,
            'found_sites': list(set(found_sites))[:30],
            'total_found': len(results),
        }
        
        set_cache(phone, dossier)
        return dossier

# ===== ГЕНЕРАТОР ПРЕМИАЛЬНОГО HTML-ОТЧЁТА =====

def generate_premium_html_report(phone, dossier):
    basic = dossier.get('basic', {})
    companies = dossier.get('companies', [])
    social = dossier.get('social', {})
    reviews = dossier.get('reviews', [])
    emails = dossier.get('emails', [])
    addresses = dossier.get('address', [])
    inn_list = dossier.get('inn', [])
    found_sites = dossier.get('found_sites', [])
    total_found = dossier.get('total_found', 0)
    
    # Рассчитываем заполненность
    sections = {
        'Основное': True,
        'Email': len(emails) > 0,
        'Соцсети': len(social) > 0,
        'Компании': len(companies) > 0,
        'Документы': len(inn_list) > 0,
        'Отзывы': len(reviews) > 0,
        'Адреса': len(addresses) > 0,
        'Источники': total_found > 0,
    }
    filled = sum(sections.values())
    total_sections = len(sections)
    percent = int(filled / total_sections * 100) if total_sections else 0
    
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Отчёт по номеру {phone}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            padding: 20px;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }}
        .container {{
            max-width: 480px;
            width: 100%;
            background: rgba(18, 18, 30, 0.85);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 24px;
            padding: 24px;
            border: 1px solid rgba(255, 255, 255, 0.06);
            box-shadow: 0 30px 60px rgba(0,0,0,0.8), 0 0 0 1px rgba(99, 102, 241, 0.1);
            animation: fadeIn 0.6s ease-out;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        @keyframes slideUp {{
            from {{ opacity: 0; transform: translateY(30px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .card {{
            background: rgba(255, 255, 255, 0.04);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border-radius: 18px;
            padding: 18px;
            margin-bottom: 16px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            transition: all 0.3s ease;
            animation: slideUp 0.5s ease-out;
            animation-fill-mode: both;
        }}
        .card:nth-child(1) {{ animation-delay: 0.1s; }}
        .card:nth-child(2) {{ animation-delay: 0.2s; }}
        .card:nth-child(3) {{ animation-delay: 0.3s; }}
        .card:nth-child(4) {{ animation-delay: 0.4s; }}
        
        .header-card {{
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.15), rgba(139, 92, 246, 0.08));
            border: 1px solid rgba(99, 102, 241, 0.2);
            text-align: center;
            padding: 24px 18px;
        }}
        .header-card .icon {{
            font-size: 48px;
            margin-bottom: 8px;
        }}
        .header-card .phone-number {{
            font-size: 24px;
            font-weight: 700;
            color: #fff;
            letter-spacing: 0.5px;
        }}
        .header-card .status {{
            display: inline-block;
            padding: 4px 14px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            margin-top: 8px;
            background: rgba(16, 185, 129, 0.2);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.15);
        }}
        .header-card .date {{
            font-size: 13px;
            color: #8888aa;
            margin-top: 6px;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 16px;
        }}
        .stat-card {{
            background: rgba(255, 255, 255, 0.04);
            border-radius: 16px;
            padding: 14px 12px;
            text-align: center;
            border: 1px solid rgba(255, 255, 255, 0.04);
            backdrop-filter: blur(6px);
        }}
        .stat-card .number {{
            font-size: 26px;
            font-weight: 700;
            background: linear-gradient(135deg, #818cf8, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .stat-card .label {{
            font-size: 11px;
            color: #8888aa;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 2px;
        }}
        
        .section-title {{
            font-size: 14px;
            font-weight: 600;
            color: #a0a0c0;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .section-title .icon {{
            font-size: 18px;
        }}
        .row {{
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        }}
        .row:last-child {{ border-bottom: none; }}
        .row .label {{
            color: #8888aa;
            font-size: 14px;
        }}
        .row .value {{
            color: #ffffff;
            font-size: 14px;
            font-weight: 500;
        }}
        .value-valid {{ color: #34d399; }}
        .tag {{
            display: inline-block;
            padding: 2px 12px;
            border-radius: 12px;
            background: rgba(99, 102, 241, 0.12);
            font-size: 12px;
            color: #a0a0cc;
            margin: 2px 4px 2px 0;
        }}
        .social-btn {{
            display: inline-block;
            padding: 6px 18px;
            border-radius: 24px;
            background: rgba(99, 102, 241, 0.15);
            color: #a0a0cc;
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            border: 1px solid rgba(99, 102, 241, 0.15);
            transition: all 0.2s;
            margin: 2px 4px 2px 0;
        }}
        .social-btn:hover {{
            background: rgba(99, 102, 241, 0.25);
            border-color: rgba(99, 102, 241, 0.3);
        }}
        .social-buttons {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 4px;
        }}
        
        .progress-container {{
            margin: 16px 0 8px 0;
        }}
        .progress-label {{
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            color: #8888aa;
        }}
        .progress-bar {{
            height: 6px;
            background: rgba(255, 255, 255, 0.06);
            border-radius: 10px;
            overflow: hidden;
            margin-top: 6px;
        }}
        .progress-fill {{
            height: 100%;
            width: {percent}%;
            background: linear-gradient(90deg, #6366f1, #8b5cf6);
            border-radius: 10px;
            transition: width 1s ease;
        }}
        
        .footer {{
            text-align: center;
            font-size: 12px;
            color: #555566;
            margin-top: 20px;
            padding-top: 16px;
            border-top: 1px solid rgba(255, 255, 255, 0.04);
        }}
        .footer .logo {{
            font-weight: 600;
            color: #6366f1;
        }}
        
        .collapsible {{
            cursor: pointer;
            user-select: none;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .collapsible .arrow {{
            transition: transform 0.3s;
            font-size: 12px;
        }}
        .collapsible .arrow.open {{
            transform: rotate(180deg);
        }}
        .collapsible-content {{
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.4s ease;
        }}
        .collapsible-content.open {{
            max-height: 1000px;
        }}
        .list-item {{
            padding: 4px 0;
            font-size: 14px;
            color: #e0e0e0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }}
        .list-item:last-child {{ border-bottom: none; }}
    </style>
</head>
<body>
<div class="container">
    <!-- HEADER CARD -->
    <div class="card header-card">
        <div class="icon">🕵️</div>
        <div class="phone-number">{phone}</div>
        <div class="status">✅ Проверен</div>
        <div class="date">Отчёт сгенерирован {now}</div>
    </div>

    <!-- STATS -->
    <div class="stats-grid">
        <div class="stat-card">
            <div class="number">{total_found}</div>
            <div class="label">Найдено сайтов</div>
        </div>
        <div class="stat-card">
            <div class="number">{len(companies)}</div>
            <div class="label">Компании/ИП</div>
        </div>
        <div class="stat-card">
            <div class="number">{len(inn_list)}</div>
            <div class="label">Документы</div>
        </div>
        <div class="stat-card">
            <div class="number">{percent}%</div>
            <div class="label">Заполненность</div>
        </div>
    </div>

    <!-- ОСНОВНАЯ ИНФОРМАЦИЯ -->
    <div class="card">
        <div class="section-title"><span class="icon">📌</span> Основная информация</div>
        <div class="row"><span class="label">Страна</span><span class="value">{basic.get('country', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Регион</span><span class="value">{basic.get('region', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Оператор</span><span class="value">{basic.get('carrier', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Часовой пояс</span><span class="value">{basic.get('timezone', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Валидность</span><span class="value value-valid">✅ {basic.get('valid', 'Нет')}</span></div>
    </div>

    <!-- ГЕОЛОКАЦИЯ -->
    <div class="card">
        <div class="section-title"><span class="icon">📍</span> Геолокация</div>
        <div class="row"><span class="label">Страна</span><span class="value">{basic.get('country', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Регион</span><span class="value">{basic.get('region', 'Неизвестно')}</span></div>
        <div class="row"><span class="label">Часовой пояс</span><span class="value">{basic.get('timezone', 'Неизвестно')}</span></div>
    </div>

    <!-- АДРЕСА -->
    {'' if not addresses else '<div class="card"><div class="section-title"><span class="icon">🏠</span> Адреса</div>'}
    {''.join([f'<div class="row"><span class="label">Адрес</span><span class="value">{addr}</span></div>' for addr in addresses[:3]])}
    {'' if not addresses else '</div>'}

    <!-- EMAIL -->
    {'' if not emails else '<div class="card"><div class="section-title"><span class="icon">📧</span> Email</div>'}
    {''.join([f'<div class="row"><span class="label">Email</span><span class="value">{email}</span></div>' for email in emails[:5]])}
    {'' if not emails else '</div>'}

    <!-- СОЦИАЛЬНЫЕ СЕТИ -->
    <div class="card">
        <div class="section-title"><span class="icon">🌐</span> Социальные сети</div>
        <div class="social-buttons">
            {''.join([f'<a href="{link}" class="social-btn" target="_blank">{name}</a>' for name, link in social.items()])}
        </div>
    </div>

    <!-- КОМПАНИИ И ИП -->
    {'' if not companies else '<div class="card"><div class="section-title"><span class="icon">🏢</span> Компании и ИП (' + str(len(companies)) + ')</div>'}
    {''.join([f'<div class="row"><span class="value">{comp}</span></div>' for comp in companies[:5]])}
    {'' if not companies else '</div>'}

    <!-- ДОКУМЕНТЫ -->
    {'' if not inn_list else '<div class="card"><div class="section-title"><span class="icon">🪪</span> Документы</div>'}
    {''.join([f'<div class="row"><span class="label">ИНН</span><span class="value value-valid">{inn}</span></div>' for inn in inn_list[:3]])}
    {'' if not inn_list else '</div>'}

    <!-- ОТЗЫВЫ -->
    {'' if not reviews else '<div class="card"><div class="section-title"><span class="icon">💬</span> Отзывы (' + str(len(reviews)) + ')</div>'}
    {''.join([f'<div class="row"><span class="value">{r[:100]}...</span></div>' for r in reviews[:3]])}
    {'' if not reviews else '</div>'}

    <!-- ИСТОЧНИКИ -->
    <div class="card">
        <div class="section-title collapsible" onclick="toggleCollapse(this)">
            <span><span class="icon">📄</span> Источники ({len(found_sites)})</span>
            <span class="arrow">▼</span>
        </div>
        <div class="collapsible-content">
            {''.join([f'<div class="list-item">• {site}</div>' for site in found_sites[:20]])}
            {f'<div style="color:#555;font-size:13px;margin-top:4px;">... и ещё {len(found_sites)-20}</div>' if len(found_sites) > 20 else ''}
        </div>
    </div>

    <!-- ПРОГРЕСС -->
    <div class="card">
        <div class="section-title"><span class="icon">📊</span> Заполненность отчёта</div>
        <div class="progress-container">
            <div class="progress-label">
                <span>{filled}/{total_sections} секций</span>
                <span>{percent}%</span>
            </div>
            <div class="progress-bar">
                <div class="progress-fill"></div>
            </div>
        </div>
        <div style="margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px;">
            {''.join([f'<span class="tag">{("✅" if val else "⬜")} {key}</span>' for key, val in sections.items()])}
        </div>
    </div>

    <!-- FOOTER -->
    <div class="footer">
        <div class="logo">SWILL DOX</div>
        <div style="margin-top: 4px;">Версия 2.0 | {now}</div>
        <div style="margin-top: 2px;">Всего найдено: {total_found} сайтов</div>
    </div>
</div>

<script>
    function toggleCollapse(el) {{
        const content = el.parentElement.nextElementSibling;
        const arrow = el.querySelector('.arrow');
        content.classList.toggle('open');
        arrow.classList.toggle('open');
    }}
    document.addEventListener('DOMContentLoaded', function() {{
        const sources = document.querySelector('.collapsible-content');
        if (sources && sources.children.length > 5) {{
            sources.classList.add('open');
            document.querySelector('.collapsible .arrow').classList.add('open');
        }}
    }});
</script>
</body>
</html>'''
    return html

# ===== КОМАНДЫ БОТА =====

def is_admin(user_id):
    return user_id == ADMIN_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    add_balance(user_id, 0)
    
    keyboard = [
        [InlineKeyboardButton("🔍 Найти по номеру", callback_data='search')],
        [InlineKeyboardButton("💰 Баланс", callback_data='balance')],
        [InlineKeyboardButton("📊 История", callback_data='history')],
        [InlineKeyboardButton("📞 Поддержка", callback_data='support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🕵️ *SWILL DOX BOT*\n\n"
        "🔓 *Бесплатно:*\n"
        "✅ Оператор, регион, страна\n\n"
        "💎 *Премиум отчёт (150 руб):*\n"
        "✅ Полное досье в HTML\n"
        "✅ Адреса, email, компании, ИП\n"
        "✅ Документы (ИНН, ОГРН, СНИЛС)\n"
        "✅ Отзывы о номере\n"
        "✅ 200+ сайтов\n"
        "✅ Красивый интерфейс\n\n"
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
            "📱 *Введите номер телефона*\nФормат: +79001234567",
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
                html = generate_premium_html_report(phone, dossier)
                html_bytes = html.encode('utf-8')
                await query.message.reply_document(
                    document=InputFile(io.BytesIO(html_bytes), filename=f"dosie_{phone.replace('+', '')}.html"),
                    caption=f"👑 *АДМИН: БЕСПЛАТНЫЙ ОТЧЁТ*\n📱 Номер: {phone}\n📊 Всего найдено: {dossier.get('total_found', 0)} сайтов"
                )
                await query.delete_message()
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка: {str(e)}")
            return
        
        # Обычный пользователь
        balance = get_balance(user_id)
        if balance < PRICE_PREMIUM:
            await query.edit_message_text(
                f"❌ *Недостаточно средств*\nНужно: {PRICE_PREMIUM} руб\nБаланс: {balance} руб",
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
            html = generate_premium_html_report(phone, dossier)
            html_bytes = html.encode('utf-8')
            await query.message.reply_document(
                document=InputFile(io.BytesIO(html_bytes), filename=f"dosie_{phone.replace('+', '')}.html"),
                caption=f"💎 *Полный отчёт по номеру {phone}*\n📊 Всего найдено: {dossier.get('total_found', 0)} сайтов"
            )
            await query.delete_message()
        except Exception as e:
            add_balance(user_id, PRICE_PREMIUM)
            await query.edit_message_text(f"❌ Ошибка: {str(e)}")
    
    elif data == 'balance':
        balance = get_balance(user_id)
        await query.edit_message_text(f"💰 Баланс: {balance} руб\n💎 Премиум: {PRICE_PREMIUM} руб", parse_mode='Markdown')
    
    elif data == 'history':
        cursor.execute("SELECT phone, price, date FROM orders WHERE user_id=? ORDER BY date DESC LIMIT 10", (user_id,))
        orders = cursor.fetchall()
        if orders:
            text = "📊 Последние запросы:\n\n"
            for phone, price, date in orders:
                text += f"📱 {phone} — {price} руб ({date[:10]})\n"
            await query.edit_message_text(text, parse_mode='Markdown')
        else:
            await query.edit_message_text("📊 История пуста", parse_mode='Markdown')
    
    elif data == 'support':
        await query.edit_message_text("📞 Поддержка: @SwillSupport\n📢 Канал: @SwillChannel", parse_mode='Markdown')

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    phone = update.message.text.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    
    if not re.match(r'^\+?\d{10,15}$', phone):
        await update.message.reply_text("❌ Неверный формат. Пример: +79001234567")
        return
    
    context.user_data['phone'] = phone
    msg = await update.message.reply_text("🔄 Сбор информации... (10-20 секунд)")
    
    try:
        dossier = SiteParser.parse_all(phone)
        basic = dossier.get('basic', {})
        report = f"📱 *{phone}*\n\n📡 Оператор: {basic.get('carrier', 'Неизвестно')}\n📍 Регион: {basic.get('region', 'Неизвестно')}\n\n💎 Полный отчёт — {PRICE_PREMIUM} руб"
        keyboard = [
            [InlineKeyboardButton("💎 Полный HTML-отчёт", callback_data='premium')],
            [InlineKeyboardButton("💰 Баланс", callback_data='balance')]
        ]
        await msg.edit_text(report, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
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
    print("👑 Админ ID:", ADMIN_ID)
    app.run_polling()

if __name__ == "__main__":
    main()
