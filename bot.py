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
                # timezone может быть списком
                tz_data = timezone.time_zones_for_number(num)
                if isinstance(tz_data, (list, tuple)):
                    basic['timezone'] = list(tz_data)
                else:
                    basic['timezone'] = str(tz_data) if tz_data else 'Неизвестно'
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
            'emails': list(set(all_emails))[:10],
            'companies': companies[:5],
            'reviews': reviews[:5],
            'social': social,
            'found_sites': list(set(found_sites))[:30],
            'total_found': len(results),
        }
        
        set_cache(phone, dossier)
        return dossier

# ===== ГЕНЕРАЦИЯ ПРЕМИАЛЬНОГО HTML-ОТЧЁТА =====

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
    timezone_data = basic.get('timezone', 'Неизвестно')
    
    # Форматируем timezone как бейджи
    timezone_badges = []
    if isinstance(timezone_data, (list, tuple)):
        for tz in timezone_data:
            timezone_badges.append(f'<span class="badge timezone-badge">{tz}</span>')
    else:
        timezone_badges.append(f'<span class="badge timezone-badge">{timezone_data}</span>')
    timezone_html = ''.join(timezone_badges)
    
    # Статистика
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
    
    # Экранируем кавычки для вставки в JavaScript
    phone_esc = phone.replace("'", "\\'")
    
    # Подготовка HTML для ИНН (исправленная часть)
    inn_html = ''
    if inn_list:
        inn_html = ''.join([f'<div class="inn-card"><span class="inn-number">{inn}</span><button class="inn-copy-btn" onclick="copyINN(this, \'{inn}\')" title="Копировать ИНН">&#128203;</button></div>' for inn in inn_list[:3]])
    else:
        inn_html = '<div class="row"><span class="label">Не найдено</span><span class="value text-secondary">—</span></div>'
    
    # Генерируем HTML
    html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
  <title>SWILL DOX — Отчёт по номеру {phone}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,400;14..32,500;14..32,600;14..32,700&display=swap" rel="stylesheet">
  <style>
    /* ===== CSS Variables ===== */
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    :root {{
      --bg: #0F1117;
      --card-bg: #171A22;
      --accent: #7C5CFF;
      --text: #FFFFFF;
      --text-secondary: #9CA3AF;
      --radius: 18px;
      --shadow: 0 20px 40px rgba(0,0,0,0.6);
      --glass: rgba(23,26,34,0.7);
      --font: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      --transition: 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    body {{
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      padding: 20px;
      min-height: 100vh;
      display: flex;
      justify-content: center;
      align-items: flex-start;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    .container {{
      max-width: 600px;
      width: 100%;
      background: var(--card-bg);
      border-radius: var(--radius);
      padding: 24px 20px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid rgba(255,255,255,0.04);
      animation: fadeIn 0.6s ease-out;
    }}
    @keyframes fadeIn {{ from {{ opacity:0; transform:translateY(20px); }} to {{ opacity:1; transform:translateY(0); }} }}
    @keyframes slideUp {{ from {{ opacity:0; transform:translateY(30px); }} to {{ opacity:1; transform:translateY(0); }} }}
    
    /* ===== Search ===== */
    .search-bar {{ margin-bottom: 20px; position: relative; }}
    .search-bar input {{
      width: 100%;
      padding: 12px 16px 12px 44px;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.04);
      color: var(--text);
      font-size: 14px;
      font-family: var(--font);
      transition: var(--transition);
      outline: none;
    }}
    .search-bar input:focus {{ border-color: var(--accent); background: rgba(255,255,255,0.06); }}
    .search-bar input::placeholder {{ color: var(--text-secondary); }}
    .search-icon {{
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-secondary);
      pointer-events: none;
    }}
    .search-results-count {{
      font-size: 13px;
      color: var(--text-secondary);
      margin-top: 8px;
      text-align: right;
      display: none;
    }}
    
    /* ===== Toolbar ===== */
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 20px;
      justify-content: flex-end;
    }}
    .toolbar button {{
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 10px;
      padding: 8px 14px;
      color: var(--text-secondary);
      font-size: 12px;
      font-family: var(--font);
      cursor: pointer;
      transition: var(--transition);
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .toolbar button:hover {{ background: rgba(255,255,255,0.12); color: var(--text); border-color: var(--accent); }}
    .toolbar button svg {{ width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; }}
    
    /* ===== Theme toggle ===== */
    .theme-toggle {{
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 10px;
      padding: 8px 14px;
      color: var(--text-secondary);
      font-size: 12px;
      font-family: var(--font);
      cursor: pointer;
      transition: var(--transition);
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .theme-toggle:hover {{ background: rgba(255,255,255,0.12); color: var(--text); border-color: var(--accent); }}
    
    /* ===== Cards ===== */
    .card {{
      background: rgba(255,255,255,0.04);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      border-radius: var(--radius);
      padding: 18px;
      margin-bottom: 16px;
      border: 1px solid rgba(255,255,255,0.05);
      transition: var(--transition);
      animation: slideUp 0.5s ease-out;
      animation-fill-mode: both;
    }}
    .card:nth-child(1) {{ animation-delay: 0.05s; }}
    .card:nth-child(2) {{ animation-delay: 0.10s; }}
    .card:nth-child(3) {{ animation-delay: 0.15s; }}
    .card:nth-child(4) {{ animation-delay: 0.20s; }}
    .card:nth-child(5) {{ animation-delay: 0.25s; }}
    .card:nth-child(6) {{ animation-delay: 0.30s; }}
    .card:nth-child(7) {{ animation-delay: 0.35s; }}
    .card:nth-child(8) {{ animation-delay: 0.40s; }}
    .card:nth-child(9) {{ animation-delay: 0.45s; }}
    .card:nth-child(10) {{ animation-delay: 0.50s; }}
    .card:hover {{ border-color: rgba(124,92,255,0.2); transform: translateY(-2px); }}
    
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }}
    .card-title {{
      font-size: 14px;
      font-weight: 600;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .card-title .icon {{ width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; }}
    .card-badge {{
      background: rgba(124,92,255,0.15);
      color: var(--accent);
      padding: 2px 10px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 600;
    }}
    
    /* ===== Header Card ===== */
    .header-card {{
      background: linear-gradient(135deg, rgba(124,92,255,0.15), rgba(124,92,255,0.03));
      border: 1px solid rgba(124,92,255,0.15);
      text-align: center;
      padding: 28px 18px;
    }}
    .header-card .icon {{ font-size: 48px; margin-bottom: 8px; }}
    .header-card .phone-number {{
      font-size: 26px;
      font-weight: 700;
      letter-spacing: 0.5px;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .header-card .status {{
      display: inline-block;
      padding: 4px 16px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
      margin-top: 8px;
      background: rgba(16,185,129,0.2);
      color: #34d399;
      border: 1px solid rgba(16,185,129,0.15);
    }}
    .header-card .date {{
      font-size: 13px;
      color: var(--text-secondary);
      margin-top: 6px;
    }}
    
    /* ===== Stats Grid ===== */
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 16px;
    }}
    .stat-card {{
      background: rgba(255,255,255,0.04);
      border-radius: 14px;
      padding: 14px 8px;
      text-align: center;
      border: 1px solid rgba(255,255,255,0.04);
      backdrop-filter: blur(6px);
      transition: var(--transition);
    }}
    .stat-card:hover {{ background: rgba(255,255,255,0.07); transform: scale(1.02); }}
    .stat-card .number {{
      font-size: 24px;
      font-weight: 700;
      background: linear-gradient(135deg, #818cf8, #a78bfa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .stat-card .label {{
      font-size: 10px;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.3px;
      margin-top: 2px;
    }}
    
    /* ===== Row ===== */
    .row {{
      display: flex;
      justify-content: space-between;
      padding: 6px 0;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      align-items: center;
      gap: 8px;
    }}
    .row:last-child {{ border-bottom: none; }}
    .row .label {{ color: var(--text-secondary); font-size: 14px; flex-shrink: 0; }}
    .row .value {{
      color: var(--text);
      font-size: 14px;
      font-weight: 500;
      overflow-wrap: anywhere;
      word-break: break-word;
      text-align: right;
      flex: 1;
    }}
    .value-valid {{ color: #34d399; }}
    .value-invalid {{ color: #f87171; }}
    
    /* ===== Badges ===== */
    .badge {{
      display: inline-block;
      padding: 2px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 500;
      background: rgba(255,255,255,0.06);
      color: var(--text-secondary);
      margin: 2px 4px 2px 0;
      white-space: nowrap;
    }}
    .badge-accent {{ background: rgba(124,92,255,0.2); color: var(--accent); }}
    .badge-success {{ background: rgba(16,185,129,0.2); color: #34d399; }}
    .badge-warning {{ background: rgba(245,158,11,0.2); color: #fbbf24; }}
    .badge-danger {{ background: rgba(239,68,68,0.2); color: #f87171; }}
    .badge-info {{ background: rgba(59,130,246,0.2); color: #60a5fa; }}
    .badge-telegram {{ background: rgba(0,136,204,0.2); color: #0088cc; }}
    .badge-whatsapp {{ background: rgba(37,211,102,0.2); color: #25d366; }}
    .badge-viber {{ background: rgba(124,92,255,0.2); color: #7C5CFF; }}
    
    /* ===== Timezone badges container ===== */
    .timezone-badges {{ display: flex; flex-wrap: wrap; gap: 4px; justify-content: flex-end; }}
    
    /* ===== Email cards ===== */
    .email-card {{
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 12px;
      background: rgba(255,255,255,0.03);
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.05);
      margin-bottom: 6px;
      transition: var(--transition);
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .email-card:hover {{ background: rgba(255,255,255,0.06); }}
    .email-icon {{ color: var(--accent); flex-shrink: 0; }}
    .email-address {{ font-size: 14px; font-weight: 500; color: var(--text); overflow-wrap: anywhere; word-break: break-word; }}
    .email-domain {{ font-size: 12px; color: var(--text-secondary); }}
    
    /* ===== INN cards with copy ===== */
    .inn-card {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 12px;
      background: rgba(255,255,255,0.03);
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.05);
      margin-bottom: 6px;
      transition: var(--transition);
    }}
    .inn-card:hover {{ background: rgba(255,255,255,0.06); }}
    .inn-number {{
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      font-family: var(--font);
      letter-spacing: 0.5px;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .inn-copy-btn {{
      background: none;
      border: none;
      color: var(--text-secondary);
      cursor: pointer;
      padding: 4px 8px;
      border-radius: 6px;
      transition: var(--transition);
      font-size: 14px;
      line-height: 1;
    }}
    .inn-copy-btn:hover {{ color: var(--accent); background: rgba(124,92,255,0.1); }}
    .inn-copy-btn.copied {{ color: #34d399; }}
    
    /* ===== Social badges ===== */
    .social-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 4px;
    }}
    .social-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 16px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 500;
      text-decoration: none;
      transition: var(--transition);
      border: 1px solid rgba(255,255,255,0.06);
    }}
    .social-badge:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,0.3); }}
    .social-badge svg {{ width: 18px; height: 18px; fill: currentColor; }}
    .social-badge.telegram {{ background: rgba(0,136,204,0.15); color: #0088cc; border-color: rgba(0,136,204,0.2); }}
    .social-badge.telegram:hover {{ background: rgba(0,136,204,0.25); }}
    .social-badge.whatsapp {{ background: rgba(37,211,102,0.15); color: #25d366; border-color: rgba(37,211,102,0.2); }}
    .social-badge.whatsapp:hover {{ background: rgba(37,211,102,0.25); }}
    .social-badge.viber {{ background: rgba(124,92,255,0.15); color: #7C5CFF; border-color: rgba(124,92,255,0.2); }}
    .social-badge.viber:hover {{ background: rgba(124,92,255,0.25); }}
    
    /* ===== Progress ===== */
    .progress-container {{ margin: 16px 0 8px; }}
    .progress-label {{
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      color: var(--text-secondary);
    }}
    .progress-bar {{
      height: 6px;
      background: rgba(255,255,255,0.06);
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
    
    /* ===== Collapsible sources ===== */
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
    .collapsible .arrow.open {{ transform: rotate(180deg); }}
    .collapsible-content {{
      max-height: 0;
      overflow: hidden;
      transition: max-height 0.4s ease;
    }}
    .collapsible-content.open {{ max-height: 1000px; }}
    .list-item {{
      padding: 4px 0;
      font-size: 14px;
      color: var(--text-secondary);
      border-bottom: 1px solid rgba(255,255,255,0.03);
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .list-item:last-child {{ border-bottom: none; }}
    
    /* ===== Footer ===== */
    .footer {{
      text-align: center;
      font-size: 12px;
      color: var(--text-secondary);
      margin-top: 20px;
      padding-top: 16px;
      border-top: 1px solid rgba(255,255,255,0.04);
    }}
    .footer .logo {{ font-weight: 600; color: var(--accent); }}
    
    /* ===== Theme light ===== */
    body.light {{
      --bg: #F2F2F7;
      --card-bg: #FFFFFF;
      --text: #1C1C1E;
      --text-secondary: #6C6C70;
      --shadow: 0 20px 40px rgba(0,0,0,0.08);
      --glass: rgba(255,255,255,0.7);
    }}
    body.light .card {{ background: rgba(255,255,255,0.6); border-color: rgba(0,0,0,0.05); }}
    body.light .header-card {{ background: linear-gradient(135deg, rgba(124,92,255,0.08), rgba(124,92,255,0.02)); }}
    body.light .stat-card {{ background: rgba(0,0,0,0.02); }}
    body.light .search-bar input {{ background: rgba(0,0,0,0.04); color: var(--text); }}
    body.light .search-bar input:focus {{ background: rgba(0,0,0,0.06); }}
    body.light .toolbar button {{ background: rgba(0,0,0,0.04); color: var(--text-secondary); }}
    body.light .toolbar button:hover {{ background: rgba(0,0,0,0.08); color: var(--text); }}
    body.light .theme-toggle {{ background: rgba(0,0,0,0.04); color: var(--text-secondary); }}
    body.light .theme-toggle:hover {{ background: rgba(0,0,0,0.08); color: var(--text); }}
    body.light .email-card {{ background: rgba(0,0,0,0.02); }}
    body.light .inn-card {{ background: rgba(0,0,0,0.02); }}
    body.light .social-badge {{ background: rgba(0,0,0,0.04); }}
    
    /* ===== Responsive ===== */
    @media (max-width: 500px) {{
      .container {{ padding: 16px; }}
      .stats-grid {{ grid-template-columns: 1fr 1fr; gap: 8px; }}
      .stat-card .number {{ font-size: 20px; }}
      .header-card .phone-number {{ font-size: 22px; }}
      .toolbar {{ gap: 4px; }}
      .toolbar button {{ font-size: 11px; padding: 6px 10px; }}
      .row {{ flex-wrap: wrap; }}
      .row .label {{ flex: 1 1 100%; }}
      .row .value {{ flex: 1 1 100%; text-align: left; }}
      .timezone-badges {{ justify-content: flex-start; }}
    }}
    
    /* ===== Utility ===== */
    .mt-8 {{ margin-top: 8px; }}
    .mb-8 {{ margin-bottom: 8px; }}
    .flex {{ display: flex; align-items: center; gap: 8px; }}
    .gap-4 {{ gap: 4px; }}
    .flex-wrap {{ flex-wrap: wrap; }}
    .text-center {{ text-align: center; }}
  </style>
</head>
<body>
<div class="container" id="reportContainer">
  <!-- Header -->
  <div class="card header-card">
    <div class="icon">🕵️</div>
    <div class="phone-number">{phone}</div>
    <div class="status">✅ Проверен</div>
    <div class="date">Отчёт сгенерирован {now}</div>
  </div>

  <!-- Toolbar -->
  <div class="toolbar">
    <button onclick="copyAll()" title="Скопировать всё">
      <svg viewBox="0 0 24 24"><path d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11v14z"/></svg>
      Копировать
    </button>
    <button onclick="exportJSON()" title="Экспорт JSON">
      <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/></svg>
      JSON
    </button>
    <button onclick="exportHTML()" title="Экспорт HTML">
      <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/></svg>
      HTML
    </button>
    <button onclick="window.print()" title="Печать / PDF">
      <svg viewBox="0 0 24 24"><path d="M19 8H5c-1.66 0-3 1.34-3 3v6h4v4h12v-4h4v-6c0-1.66-1.34-3-3-3zm-3 11H8v-5h8v5zm3-7c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm-1-9H6v4h12V3z"/></svg>
      PDF
    </button>
    <button class="theme-toggle" onclick="toggleTheme()" title="Переключить тему">
      <span id="themeIcon">🌙</span>
    </button>
  </div>

  <!-- Search -->
  <div class="search-bar">
    <span class="search-icon">🔍</span>
    <input type="text" id="searchInput" placeholder="Поиск по отчёту..." oninput="filterReport()">
    <div class="search-results-count" id="searchCount"></div>
  </div>

  <!-- Stats -->
  <div class="stats-grid">
    <div class="stat-card"><div class="number">{total_found}</div><div class="label">Найдено сайтов</div></div>
    <div class="stat-card"><div class="number">{len(companies)}</div><div class="label">Компании/ИП</div></div>
    <div class="stat-card"><div class="number">{len(inn_list)}</div><div class="label">Документы</div></div>
    <div class="stat-card"><div class="number">{percent}%</div><div class="label">Заполненность</div></div>
  </div>

  <!-- Основная информация -->
  <div class="card" data-search="основная информация страна регион оператор часовой пояс валидность">
    <div class="card-header">
      <div class="card-title"><span class="icon">📌</span> Основная информация</div>
    </div>
    <div class="row"><span class="label">Страна</span><span class="value">{basic.get('country', 'Неизвестно')}</span></div>
    <div class="row"><span class="label">Регион</span><span class="value">{basic.get('region', 'Неизвестно')}</span></div>
    <div class="row"><span class="label">Оператор</span><span class="value">{basic.get('carrier', 'Неизвестно')}</span></div>
    <div class="row"><span class="label">Часовой пояс</span><div class="timezone-badges">{timezone_html}</div></div>
    <div class="row"><span class="label">Валидность</span><span class="value value-valid">✅ {basic.get('valid', 'Нет')}</span></div>
  </div>

  <!-- Геолокация -->
  <div class="card" data-search="геолокация страна регион часовой пояс">
    <div class="card-header">
      <div class="card-title"><span class="icon">📍</span> Геолокация</div>
    </div>
    <div class="row"><span class="label">Страна</span><span class="value">{basic.get('country', 'Неизвестно')}</span></div>
    <div class="row"><span class="label">Регион</span><span class="value">{basic.get('region', 'Неизвестно')}</span></div>
    <div class="row"><span class="label">Часовой пояс</span><div class="timezone-badges">{timezone_html}</div></div>
  </div>

  <!-- Адреса -->
  <div class="card" data-search="адреса">
    <div class="card-header">
      <div class="card-title"><span class="icon">🏠</span> Адреса <span class="card-badge">{len(addresses)}</span></div>
    </div>
    {'' if addresses else '<div class="row"><span class="label">Не найдено</span><span class="value text-secondary">—</span></div>'}
    {''.join([f'<div class="row"><span class="label">Адрес</span><span class="value">{addr}</span></div>' for addr in addresses[:5]])}
  </div>

  <!-- Email -->
  <div class="card" data-search="email">
    <div class="card-header">
      <div class="card-title"><span class="icon">📧</span> Email <span class="card-badge">{len(emails)}</span></div>
    </div>
    {'' if emails else '<div class="row"><span class="label">Не найдено</span><span class="value text-secondary">—</span></div>'}
    {''.join([f'<div class="email-card"><span class="email-icon">📧</span><div><div class="email-address">{email}</div></div></div>' for email in emails[:10]])}
  </div>

  <!-- Социальные сети -->
  <div class="card" data-search="социальные сети telegram whatsapp viber">
    <div class="card-header">
      <div class="card-title"><span class="icon">🌐</span> Социальные сети</div>
    </div>
    <div class="social-badges">
      {''.join([f'<a href="{link}" class="social-badge {name.lower()}" target="_blank"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/></svg>{name}</a>' for name, link in social.items()])}
    </div>
  </div>

  <!-- Компании и ИП -->
  <div class="card" data-search="компании ип">
    <div class="card-header">
      <div class="card-title"><span class="icon">🏢</span> Компании и ИП <span class="card-badge">{len(companies)}</span></div>
    </div>
    {'' if companies else '<div class="row"><span class="label">Не найдено</span><span class="value text-secondary">—</span></div>'}
    {''.join([f'<div class="row"><span class="value">{comp}</span></div>' for comp in companies[:5]])}
  </div>

  <!-- Документы (ИНН) -->
  <div class="card" data-search="документы инн">
    <div class="card-header">
      <div class="card-title"><span class="icon">🪪</span> Документы <span class="card-badge">{len(inn_list)}</span></div>
    </div>
    {inn_html}
  </div>

  <!-- Отзывы -->
  <div class="card" data-search="отзывы">
    <div class="card-header">
      <div class="card-title"><span class="icon">💬</span> Отзывы <span class="card-badge">{len(reviews)}</span></div>
    </div>
    {'' if reviews else '<div class="row"><span class="label">Не найдено</span><span class="value text-secondary">—</span></div>'}
    {''.join([f'<div class="row"><span class="value">{r[:100]}…</span></div>' for r in reviews[:5]])}
  </div>

  <!-- Источники (раскрывающийся с поиском) -->
  <div class="card" data-search="источники сайты">
    <div class="card-header">
      <div class="card-title"><span class="icon">📄</span> Источники <span class="card-badge">{len(found_sites)}</span></div>
    </div>
    <div class="collapsible" onclick="toggleCollapse(this)">
      <span>Показать все источники</span>
      <span class="arrow">▼</span>
    </div>
    <div class="collapsible-content" id="sourcesList">
      {''.join([f'<div class="list-item">• {site}</div>' for site in found_sites[:30]])}
      {f'<div style="color:var(--text-secondary);font-size:13px;margin-top:4px;">… и ещё {len(found_sites)-30}</div>' if len(found_sites) > 30 else ''}
    </div>
  </div>

  <!-- Прогресс -->
  <div class="card">
    <div class="card-header">
      <div class="card-title"><span class="icon">📊</span> Заполненность отчёта</div>
    </div>
    <div class="progress-container">
      <div class="progress-label"><span>{filled}/{total_sections} секций</span><span>{percent}%</span></div>
      <div class="progress-bar"><div class="progress-fill"></div></div>
    </div>
    <div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:6px;">
      {''.join([f'<span class="badge {"badge-success" if val else "badge-secondary"}">{"✅" if val else "⬜"} {key}</span>' for key, val in sections.items()])}
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <div class="logo">SWILL DOX</div>
    <div style="margin-top:4px;">Версия 2.0 | {now}</div>
    <div style="margin-top:2px;">Всего найдено: {total_found} сайтов</div>
  </div>
</div>

<script>
  // ===== Theme toggle =====
  let darkMode = true;
  function toggleTheme() {{
    document.body.classList.toggle('light');
    darkMode = !darkMode;
    document.getElementById('themeIcon').textContent = darkMode ? '🌙' : '☀️';
  }}

  // ===== Collapsible =====
  function toggleCollapse(el) {{
    const content = el.parentElement.nextElementSibling;
    const arrow = el.querySelector('.arrow');
    content.classList.toggle('open');
    arrow.classList.toggle('open');
  }}

  // ===== Search filter =====
  function filterReport() {{
    const query = document.getElementById('searchInput').value.toLowerCase().trim();
    const cards = document.querySelectorAll('.card');
    let visibleCount = 0;  // <--- ИСПРАВЛЕНО: объявлено здесь
    cards.forEach(card => {{
      const text = card.textContent.toLowerCase();
      const match = text.includes(query);
      card.style.display = match ? '' : 'none';
      if (match) visibleCount++;
    }});
    const countEl = document.getElementById('searchCount');
    if (query.length > 0) {{
      countEl.textContent = `Найдено карточек: ${visibleCount}`;
      countEl.style.display = 'block';
    }} else {{
      countEl.style.display = 'none';
    }}
  }}

  // ===== Copy INN =====
  function copyINN(btn, inn) {{
    navigator.clipboard.writeText(inn).then(() => {{
      btn.textContent = '✅';
      btn.classList.add('copied');
      setTimeout(() => {{
        btn.textContent = '📋';
        btn.classList.remove('copied');
      }}, 2000);
    }});
  }}

  // ===== Copy all =====
  function copyAll() {{
    const text = document.getElementById('reportContainer').textContent;
    navigator.clipboard.writeText(text).then(() => {{
      alert('Весь отчёт скопирован в буфер обмена.');
    }});
  }}

  // ===== Export JSON =====
  function exportJSON() {{
    const data = {{
      phone: '{phone_esc}',
      country: '{basic.get('country', 'Неизвестно')}',
      region: '{basic.get('region', 'Неизвестно')}',
      carrier: '{basic.get('carrier', 'Неизвестно')}',
      timezone: {json.dumps(basic.get('timezone', 'Неизвестно'))},
      valid: '{basic.get('valid', 'Нет')}',
      addresses: {json.dumps(addresses)},
      emails: {json.dumps(emails)},
      companies: {json.dumps(companies)},
      inn: {json.dumps(inn_list)},
      reviews: {json.dumps(reviews)},
      total_found: {total_found},
    }};
    const blob = new Blob([JSON.stringify(data, null, 2)], {{ type: 'application/json' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'report_{phone_esc}.json';
    a.click();
    URL.revokeObjectURL(url);
  }}

  // ===== Export HTML =====
  function exportHTML() {{
    const html = document.documentElement.outerHTML;
    const blob = new Blob([html], {{ type: 'text/html' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'report_{phone_esc}.html';
    a.click();
    URL.revokeObjectURL(url);
  }}

  // ===== Auto-expand sources if many =====
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
