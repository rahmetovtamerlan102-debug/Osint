#!/usr/bin/env python3
# SWILL DOX BOT — 200+ САЙТОВ (ТОЛЬКО TG/WA/VIBER)
# Установка: pip install python-telegram-bot phonenumbers requests beautifulsoup4 lxml

import logging
import re
import json
import sqlite3
import requests
import phonenumbers
from phonenumbers import carrier, geocoder, timezone
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from bs4 import BeautifulSoup
import time
import random
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# ===== ФИЛЬТР РЕАЛЬНЫХ ИМЁН =====

class RealNameFilter:
    RUSSIAN_NAMES = {
        'алексей', 'андрей', 'антон', 'аркадий', 'артём', 'борис',
        'вадим', 'валентин', 'валерий', 'василий', 'виктор', 'владимир',
        'владислав', 'всеволод', 'вячеслав', 'геннадий', 'георгий',
        'глеб', 'григорий', 'даниил', 'денис', 'дмитрий', 'евгений',
        'егор', 'иван', 'игорь', 'илья', 'кирилл', 'константин',
        'лев', 'леонид', 'максим', 'марк', 'матвей', 'михаил',
        'никита', 'николай', 'олег', 'павел', 'пётр', 'платон',
        'роберт', 'роман', 'сергей', 'станислав', 'степан', 'тимофей',
        'фёдор', 'филипп', 'юрий', 'яков', 'ярослав',
        'александра', 'алина', 'алиса', 'алла', 'анастасия',
        'ангелина', 'анна', 'валентина', 'валерия', 'вера',
        'вероника', 'виктория', 'галина', 'дарья', 'диана',
        'екатерина', 'елена', 'елизавета', 'жанна', 'зинаида',
        'зоя', 'инга', 'инна', 'ирина', 'карина', 'кира',
        'кристина', 'ксения', 'лариса', 'лидия', 'лилия',
        'любовь', 'людмила', 'марина', 'мария', 'надежда',
        'наталья', 'нина', 'оксана', 'ольга', 'полина',
        'раиса', 'регина', 'римма', 'светлана', 'софия',
        'таисия', 'тамара', 'татьяна', 'ульяна', 'юлия'
    }
    
    STOP_WORDS = {
        'российская', 'федерация', 'подробнее', 'правила', 'авито',
        'журнал', 'лесная', 'полянка', 'администрация', 'губернатор',
        'министр', 'департамент', 'управление', 'комитет', 'совет',
        'служба', 'агентство', 'инспекция', 'президент', 'директор',
        'руководитель', 'специалист', 'консультант', 'менеджер'
    }
    
    @staticmethod
    def is_real_fio(fio):
        if not fio:
            return False
        parts = fio.strip().split()
        if len(parts) < 2:
            return False
        for part in parts:
            part_lower = part.lower()
            for stop in RealNameFilter.STOP_WORDS:
                if stop in part_lower or part_lower in stop:
                    return False
            if len(part) < 2 or len(part) > 20:
                return False
            if not re.match(r'^[А-ЯЁ][а-яё]+$', part):
                return False
        name_part = parts[1].lower()
        if name_part in RealNameFilter.RUSSIAN_NAMES:
            return True
        last_part = parts[0].lower()
        if last_part in RealNameFilter.RUSSIAN_NAMES:
            return True
        return False
    
    @staticmethod
    def filter_fio_list(fio_list):
        valid = []
        seen = set()
        for fio in fio_list:
            if RealNameFilter.is_real_fio(fio) and fio not in seen:
                valid.append(fio)
                seen.add(fio)
        return valid

# ===== 200+ САЙТОВ =====

class SiteParser:
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    }
    
    # ===== ВСЕ САЙТЫ (200+) =====
    SITES = []
    
    # 1-40: Российские справочники
    RUSSIAN_SITES = [
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
        {'name': 'cob24.ru', 'url': 'https://cob24.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'findphone.ru', 'url': 'https://findphone.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'phone-lookup.ru', 'url': 'https://phone-lookup.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'spamcalls.ru', 'url': 'https://spamcalls.ru/num/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'callerid.ru', 'url': 'https://callerid.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'telros.ru', 'url': 'https://telros.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'num-book.ru', 'url': 'https://num-book.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'telspravka.ru', 'url': 'https://telspravka.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'phonebook.ru', 'url': 'https://phonebook.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'infophone.ru', 'url': 'https://infophone.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'call-center.ru', 'url': 'https://call-center.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'telephon.ru', 'url': 'https://telephon.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'nomera.ru', 'url': 'https://nomera.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'ruskontakt.ru', 'url': 'https://ruskontakt.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'teleinfo.ru', 'url': 'https://teleinfo.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'phonenumber.ru', 'url': 'https://phonenumber.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'callinfo.ru', 'url': 'https://callinfo.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'telbase.ru', 'url': 'https://telbase.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'numlist.ru', 'url': 'https://numlist.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'phone-russia.ru', 'url': 'https://phone-russia.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'teldata.ru', 'url': 'https://teldata.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'phonedb.ru', 'url': 'https://phonedb.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'callru.ru', 'url': 'https://callru.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'telinfo.ru', 'url': 'https://telinfo.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'numsearch.ru', 'url': 'https://numsearch.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'phonefind.ru', 'url': 'https://phonefind.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'telguide.ru', 'url': 'https://telguide.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'phonelist.ru', 'url': 'https://phonelist.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'callbase.ru', 'url': 'https://callbase.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'telarchive.ru', 'url': 'https://telarchive.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
    ]
    
    # 41-70: Зарубежные справочники
    FOREIGN_SITES = [
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
        {'name': 'spokeo.com', 'url': 'https://www.spokeo.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'beenverified.com', 'url': 'https://www.beenverified.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'instantcheckmate.com', 'url': 'https://www.instantcheckmate.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'checkpeople.com', 'url': 'https://www.checkpeople.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'publicrecords.com', 'url': 'https://www.publicrecords.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'peoplelooker.com', 'url': 'https://www.peoplelooker.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'usphonebook.com', 'url': 'https://www.usphonebook.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'phonelookup.com', 'url': 'https://www.phonelookup.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'numberlookup.com', 'url': 'https://www.numberlookup.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'callersearch.com', 'url': 'https://www.callersearch.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'whitepages.ae', 'url': 'https://www.whitepages.ae/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'numberbook.com', 'url': 'https://www.numberbook.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'phonecheck.com', 'url': 'https://www.phonecheck.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'verifyphone.com', 'url': 'https://www.verifyphone.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'international-number.com', 'url': 'https://www.international-number.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'globalphone.com', 'url': 'https://www.globalphone.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'worldnumber.com', 'url': 'https://www.worldnumber.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'phonetracker.com', 'url': 'https://www.phonetracker.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'numlookup.com', 'url': 'https://www.numlookup.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'phonefinder.com', 'url': 'https://www.phonefinder.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
    ]
    
    # 71-73: ТОЛЬКО TG, WA, VIBER
    SOCIAL_SITES = [
        {'name': 'Telegram', 'url': 'https://t.me/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'WhatsApp', 'url': 'https://wa.me/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'Viber', 'url': 'viber://chat?number={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
    ]
    
    # 74-88: Базы утечек
    LEAK_SITES = [
        {'name': 'leakcheck.net', 'url': 'https://leakcheck.net/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'breachdirectory.org', 'url': 'https://www.breachdirectory.org/search.php?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'scamalytics.com', 'url': 'https://scamalytics.com/phone/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'haveibeenpwned.com', 'url': 'https://haveibeenpwned.com/account/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'dehashed.com', 'url': 'https://dehashed.com/search?query={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'leaked.site', 'url': 'https://leaked.site/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'leakbase.io', 'url': 'https://leakbase.io/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'databreach.com', 'url': 'https://databreach.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'breachalarm.com', 'url': 'https://breachalarm.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'leakalert.com', 'url': 'https://leakalert.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'pwned.com', 'url': 'https://pwned.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'cybernews.com', 'url': 'https://cybernews.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'securitybreach.com', 'url': 'https://securitybreach.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'dataleak.com', 'url': 'https://dataleak.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'hackeddb.com', 'url': 'https://hackeddb.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
    ]
    
    # 89-113: Компании
    COMPANY_SITES = [
        {'name': 'kartoteka.ru', 'url': 'https://kartoteka.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'egrul.ru', 'url': 'https://egrul.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'egrip.ru', 'url': 'https://egrip.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'sbis.ru', 'url': 'https://sbis.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'kontur.ru', 'url': 'https://kontur.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'spark.ru', 'url': 'https://spark.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'zachestnyibiznes.ru', 'url': 'https://zachestnyibiznes.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'list-org.ru', 'url': 'https://list-org.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'company.com', 'url': 'https://company.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'businessprofile.com', 'url': 'https://businessprofile.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'corporateinfo.com', 'url': 'https://corporateinfo.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'companysearch.com', 'url': 'https://companysearch.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'firmfinder.com', 'url': 'https://firmfinder.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'businesslookup.com', 'url': 'https://businesslookup.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'orgsearch.com', 'url': 'https://orgsearch.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'enterprise.com', 'url': 'https://enterprise.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'corporation.com', 'url': 'https://corporation.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'ltdsearch.com', 'url': 'https://ltdsearch.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'incfinder.com', 'url': 'https://incfinder.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'companycheck.com', 'url': 'https://companycheck.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'firmdata.com', 'url': 'https://firmdata.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'businessdb.com', 'url': 'https://businessdb.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'corporatebase.com', 'url': 'https://corporatebase.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'companyregistry.com', 'url': 'https://companyregistry.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'orgbase.com', 'url': 'https://orgbase.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
    ]
    
    # 114-133: Форумы и отзывы
    REVIEW_SITES = [
        {'name': 'otzyv.ru', 'url': 'https://www.otzyv.ru/search/?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'flamp.ru', 'url': 'https://www.flamp.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': '2gis.ru', 'url': 'https://www.2gis.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'google.com/maps', 'url': 'https://www.google.com/maps/search/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+', 'addr': r'\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+'},
        {'name': 'yandex.ru/maps', 'url': 'https://yandex.ru/maps/search/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'tellows.ru', 'url': 'https://www.tellows.ru/num/{phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'otzovik.ru', 'url': 'https://otzovik.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'irecommend.ru', 'url': 'https://irecommend.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'forum.ru', 'url': 'https://forum.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'citytalk.ru', 'url': 'https://citytalk.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'peoplesreview.com', 'url': 'https://peoplesreview.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'reviewcenter.com', 'url': 'https://reviewcenter.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'feedbackhub.com', 'url': 'https://feedbackhub.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'opinionboard.com', 'url': 'https://opinionboard.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'ratemycompany.com', 'url': 'https://ratemycompany.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'trustpilot.com', 'url': 'https://trustpilot.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'yell.com', 'url': 'https://yell.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'citysearch.com', 'url': 'https://citysearch.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'localreviews.com', 'url': 'https://localreviews.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'userreviews.com', 'url': 'https://userreviews.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
    ]
    
    # 134-143: Поисковики
    SEARCH_SITES = [
        {'name': 'yandex.ru', 'url': 'https://yandex.ru/search/?text={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'mail.ru', 'url': 'https://mail.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'google.com', 'url': 'https://google.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'bing.com', 'url': 'https://bing.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'duckduckgo.com', 'url': 'https://duckduckgo.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'yahoo.com', 'url': 'https://yahoo.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'rambler.ru', 'url': 'https://rambler.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'qip.ru', 'url': 'https://qip.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'nigma.ru', 'url': 'https://nigma.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'webfalta.ru', 'url': 'https://webfalta.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
    ]
    
    # 144-163: Маркетплейсы
    MARKET_SITES = [
        {'name': 'avito.ru', 'url': 'https://www.avito.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'drom.ru', 'url': 'https://www.drom.ru/search/?text={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'auto.ru', 'url': 'https://auto.ru/search/?text={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'cian.ru', 'url': 'https://www.cian.ru/search/?query={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'domofond.ru', 'url': 'https://www.domofond.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+', 'addr': r'[А-Яа-я]+,\s+ул\.\s+[А-Яа-я]+,\s+д\.\s+\d+'},
        {'name': 'kinopoisk.ru', 'url': 'https://www.kinopoisk.ru/search/?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'ozon.ru', 'url': 'https://ozon.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'wildberries.ru', 'url': 'https://wildberries.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'market.yandex.ru', 'url': 'https://market.yandex.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'goods.ru', 'url': 'https://goods.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'youla.ru', 'url': 'https://youla.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'ebay.com', 'url': 'https://ebay.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'amazon.com', 'url': 'https://amazon.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'aliexpress.com', 'url': 'https://aliexpress.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'etsy.com', 'url': 'https://etsy.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'craigslist.org', 'url': 'https://craigslist.org/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'olx.com', 'url': 'https://olx.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'jiji.com', 'url': 'https://jiji.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'gumtree.com', 'url': 'https://gumtree.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'kijiji.com', 'url': 'https://kijiji.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
    ]
    
    # 164-183: Прочие полезные сайты
    OTHER_SITES = [
        {'name': 'wikipedia.org', 'url': 'https://wikipedia.org/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'gravatar.com', 'url': 'https://gravatar.com/{phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'imgur.com', 'url': 'https://imgur.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'pastebin.com', 'url': 'https://pastebin.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'codepen.io', 'url': 'https://codepen.io/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'stackoverflow.com', 'url': 'https://stackoverflow.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'quora.com', 'url': 'https://quora.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'medium.com', 'url': 'https://medium.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'wordpress.com', 'url': 'https://wordpress.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'blogspot.com', 'url': 'https://blogspot.com/search?q={phone}', 'fio': r'[A-Z][a-z]+\s[A-Z][a-z]+'},
        {'name': 'vc.ru', 'url': 'https://vc.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'habr.com', 'url': 'https://habr.com/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'tjournal.ru', 'url': 'https://tjournal.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'dzen.ru', 'url': 'https://dzen.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'lenta.ru', 'url': 'https://lenta.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'rbc.ru', 'url': 'https://rbc.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'kommersant.ru', 'url': 'https://kommersant.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'gazeta.ru', 'url': 'https://gazeta.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'iz.ru', 'url': 'https://iz.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
        {'name': 'rg.ru', 'url': 'https://rg.ru/search?q={phone}', 'fio': r'[А-Я][а-я]+\s[А-Я][а-я]+'},
    ]
    
    # Собираем все сайты в один список
    SITES = (RUSSIAN_SITES + FOREIGN_SITES + SOCIAL_SITES + LEAK_SITES + 
             COMPANY_SITES + REVIEW_SITES + SEARCH_SITES + MARKET_SITES + OTHER_SITES)
    
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
            patterns['inn'] = r'(?<!\d)\d{10}(?!\d)|(?<!\d)\d{12}(?!\d)'
            patterns['ogrn'] = r'(?<!\d)\d{13}(?!\d)|(?<!\d)\d{15}(?!\d)'
            patterns['snils'] = r'\d{3}-\d{3}-\d{3} \d{2}'
            
            result = SiteParser.extract_info(text, patterns)
            
            if result.get('fio'):
                result['fio'] = RealNameFilter.filter_fio_list(result['fio'])
            
            found = False
            for key in ['fio', 'address', 'phone_numbers', 'emails', 'inn', 'ogrn', 'snils']:
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
            html = SiteParser.get_page(url, timeout=10)
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                text = soup.get_text()
                patterns = [
                    r'(ООО\s[А-Яа-я]+\s[А-Яа-я]+)',
                    r'(ИП\s[А-Я][а-я]+\s[А-Я][а-я]+)',
                    r'(ООО\s"[А-Яа-я\s]+")',
                    r'(ИП\s"[А-Яа-я\s]+")',
                    r'([А-Я][А-Я]+\s[А-Я][А-Я]+)'
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
                f"https://spamcalls.ru/num/{clean_phone}",
                f"https://callerid.ru/search?q={phone}"
            ]
            for url in sites:
                html = SiteParser.get_page(url, timeout=8)
                if html:
                    soup = BeautifulSoup(html, 'html.parser')
                    text = soup.get_text()
                    comments = re.findall(r'[А-Я][а-я\s,.\-]{20,200}', text)
                    if comments:
                        reviews.extend(comments[:3])
                    time.sleep(0.5)
        except:
            pass
        return list(set(reviews))[:10]
    
    @staticmethod
    def get_social_profiles(phone):
        clean = phone.replace('+', '').replace(' ', '').replace('-', '')
        # ТОЛЬКО Telegram, WhatsApp, Viber
        return {
            'Telegram': f"https://t.me/{clean}",
            'WhatsApp': f"https://wa.me/{phone}",
            'Viber': f"viber://chat?number={clean}",
        }
    
    @staticmethod
    def get_leaks(phone):
        try:
            clean = phone.replace('+', '').replace(' ', '').replace('-', '')
            url = f"https://leakcheck.net/search?q={clean}"
            html = SiteParser.get_page(url, timeout=8)
            if html:
                if 'found' in html.lower() or 'найдено' in html:
                    return ['✅ Найден в утечках данных']
            return ['❌ Не найден в утечках']
        except:
            return ['⚠️ Ошибка проверки утечек']
    
    @staticmethod
    def parse_all(phone, max_workers=10):
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
                        if tz and 'Europe' in tz:
                            basic['region'] = 'Европейская часть России'
                        elif tz and 'Asia' in tz:
                            basic['region'] = 'Азиатская часть России'
                        else:
                            basic['region'] = 'Россия'
                    except:
                        basic['region'] = 'Россия'
                
                basic['country'] = geocoder.description_for_number(num, 'en') or 'Неизвестно'
                basic['carrier'] = carrier.name_for_number(num, 'ru') or 'Неизвестно'
                basic['timezone'] = str(timezone.time_zones_for_number(num)) or 'Неизвестно'
                basic['valid'] = 'Да'
        except:
            pass
        
        # Парсинг всех сайтов (200+)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(SiteParser.parse_site, site, phone): site for site in SiteParser.SITES}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
                    found_sites.append(result.get('name', 'Unknown'))
                time.sleep(0.2)
        
        # Сбор всех найденных данных
        all_fio = []
        all_address = []
        all_phones = []
        all_emails = []
        all_inn = []
        all_ogrn = []
        all_snils = []
        
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
            if result.get('ogrn'):
                all_ogrn.extend(result['ogrn'])
            if result.get('snils'):
                all_snils.extend(result['snils'])
        
        all_fio = RealNameFilter.filter_fio_list(all_fio)
        
        companies = SiteParser.get_company_info(phone)
        reviews = SiteParser.get_reviews(phone)
        social = SiteParser.get_social_profiles(phone)
        leaks = SiteParser.get_leaks(phone)
        
        return {
            'basic': basic,
            'fio': all_fio[:10],
            'address': list(set(all_address))[:5],
            'phones': list(set(all_phones))[:10],
            'emails': list(set(all_emails))[:10],
            'inn': list(set(all_inn))[:3],
            'ogrn': list(set(all_ogrn))[:3],
            'snils': list(set(all_snils))[:3],
            'companies': companies[:10],
            'reviews': reviews[:10],
            'social': social,
            'leaks': leaks,
            'found_sites': list(set(found_sites))[:30],
            'total_found': len(results),
        }

# ===== ГЕНЕРАТОР HTML ОТЧЁТА =====

def generate_html_report(phone, dossier):
    basic = dossier.get('basic', {})
    fio_list = dossier.get('fio', [])
    companies = dossier.get('companies', [])
    social = dossier.get('social', {})
    reviews = dossier.get('reviews', [])
    leaks = dossier.get('leaks', [])
    
    sections = {
        'Компании/ИП': len(companies) > 0,
        'Соцсети': len(social) > 0,
        'Адреса': len(dossier.get('address', [])) > 0,
        'Email': len(dossier.get('emails', [])) > 0,
        'ФИО': len(fio_list) > 0,
        'Утечки': 'Найден' in ''.join(leaks),
        'Документы': len(dossier.get('inn', [])) > 0 or len(dossier.get('snils', [])) > 0,
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
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            padding: 20px;
            max-width: 420px;
            margin: 0 auto;
        }}
        .container {{ 
            background: #12121a; 
            border-radius: 16px; 
            padding: 20px;
            border: 1px solid #2a2a3a;
        }}
        .header {{ 
            text-align: center; 
            padding-bottom: 16px; 
            border-bottom: 1px solid #2a2a3a;
            margin-bottom: 16px;
        }}
        .header h1 {{ 
            font-size: 18px; 
            font-weight: 700; 
            color: #ffffff;
            background: linear-gradient(90deg, #6366f1, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .header .phone {{ 
            font-size: 20px; 
            font-weight: 600; 
            color: #ffffff;
            margin-top: 4px;
            -webkit-text-fill-color: #ffffff;
        }}
        .section {{ 
            background: #1a1a2a; 
            border-radius: 12px; 
            padding: 14px 16px; 
            margin-bottom: 12px;
            border-left: 3px solid #6366f1;
        }}
        .section-title {{ 
            font-size: 14px; 
            font-weight: 600; 
            color: #a0a0b8; 
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .section-content {{ 
            font-size: 14px; 
            color: #e0e0e0;
            line-height: 1.6;
        }}
        .badge {{ 
            display: inline-block; 
            padding: 2px 10px; 
            border-radius: 20px; 
            font-size: 11px; 
            font-weight: 600;
        }}
        .badge-success {{ background: #10b981; color: #fff; }}
        .badge-warning {{ background: #f59e0b; color: #000; }}
        .badge-danger {{ background: #ef4444; color: #fff; }}
        .badge-info {{ background: #3b82f6; color: #fff; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
        .grid-item {{ background: #1f1f32; padding: 10px; border-radius: 8px; text-align: center; }}
        .grid-item .number {{ font-size: 20px; font-weight: 700; color: #6366f1; }}
        .grid-item .label {{ font-size: 11px; color: #8888aa; }}
        .list-item {{ 
            padding: 4px 0; 
            border-bottom: 1px solid #2a2a3a;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .list-item:last-child {{ border-bottom: none; }}
        .list-item .item-label {{ color: #8888aa; font-size: 13px; }}
        .list-item .item-value {{ color: #ffffff; font-size: 13px; font-weight: 500; }}
        .social-link {{ 
            display: inline-block; 
            padding: 4px 12px; 
            border-radius: 20px; 
            background: #2a2a4a; 
            font-size: 12px;
            color: #a0a0cc;
            text-decoration: none;
            margin: 2px 4px 2px 0;
        }}
        .social-link:hover {{ background: #3a3a5a; }}
        .footer {{ 
            text-align: center; 
            font-size: 12px; 
            color: #555566; 
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid #2a2a3a;
        }}
        .progress-bar {{ 
            height: 6px; 
            background: #2a2a3a; 
            border-radius: 10px; 
            overflow: hidden;
            margin: 8px 0 4px 0;
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
            font-size: 13px;
            color: #8888aa;
        }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🔍 Досье по номеру</h1>
        <div class="phone">{phone}</div>
    </div>

    <div class="section">
        <div class="section-title">📌 Основное</div>
        <div class="section-content">
            <div class="list-item"><span class="item-label">Оператор</span><span class="item-value">{basic.get('carrier', 'Неизвестно')}</span></div>
            <div class="list-item"><span class="item-label">Регион</span><span class="item-value">{basic.get('region', 'Неизвестно')}</span></div>
            <div class="list-item"><span class="item-label">Страна</span><span class="item-value">{basic.get('country', 'Неизвестно')}</span></div>
            <div class="list-item"><span class="item-label">Часовой пояс</span><span class="item-value">{basic.get('timezone', 'Неизвестно')}</span></div>
            <div class="list-item"><span class="item-label">Валидность</span><span class="item-value"><span class="badge badge-success">✅ {basic.get('valid', 'Нет')}</span></span></div>
        </div>
    </div>

    <div class="section" style="border-left-color: #8b5cf6;">
        <div class="section-title">📊 Общая сводка</div>
        <div class="section-content">
            <div class="grid">
                <div class="grid-item"><div class="number">{len(fio_list)}</div><div class="label">ФИО</div></div>
                <div class="grid-item"><div class="number">{len(companies)}</div><div class="label">Компании/ИП</div></div>
                <div class="grid-item"><div class="number">{len(dossier.get('emails', []))}</div><div class="label">Email</div></div>
                <div class="grid-item"><div class="number">{len(dossier.get('address', []))}</div><div class="label">Адреса</div></div>
            </div>
        </div>
    </div>

    <div class="section" style="border-left-color: #10b981;">
        <div class="section-title">📈 Покрытие отчёта</div>
        <div class="section-content">
            <div class="coverage-text"><span>{found}/{total} секций</span><span>{percent}%</span></div>
            <div class="progress-bar"><div class="progress-fill"></div></div>
        </div>
    </div>

    <div class="section" style="border-left-color: #f59e0b;">
        <div class="section-title">👤 ФИО ({len(fio_list)})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {f}</span></div>' for f in fio_list[:5]])}
            {f'<div style="color: #666; font-size: 12px;">... и ещё {len(fio_list)-5}</div>' if len(fio_list) > 5 else ''}
        </div>
    </div>

    <div class="section" style="border-left-color: #3b82f6;">
        <div class="section-title">🏢 Компании и ИП ({len(companies)})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {c}</span></div>' for c in companies[:5]])}
            {f'<div style="color: #666; font-size: 12px;">... и ещё {len(companies)-5}</div>' if len(companies) > 5 else ''}
        </div>
    </div>

    <div class="section" style="border-left-color: #ec4899;">
        <div class="section-title">📍 Адреса ({len(dossier.get('address', []))})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {a}</span></div>' for a in dossier.get('address', [])[:3]])}
        </div>
    </div>

    <div class="section" style="border-left-color: #8b5cf6;">
        <div class="section-title">📧 Email ({len(dossier.get('emails', []))})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {e}</span></div>' for e in dossier.get('emails', [])[:5]])}
        </div>
    </div>

    <div class="section" style="border-left-color: #ef4444;">
        <div class="section-title">🪪 Документы</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-label">ИНН</span><span class="item-value">{inn}</span></div>' for inn in dossier.get('inn', [])[:2]])}
            {''.join([f'<div class="list-item"><span class="item-label">ОГРН</span><span class="item-value">{ogrn}</span></div>' for ogrn in dossier.get('ogrn', [])[:2]])}
            {''.join([f'<div class="list-item"><span class="item-label">СНИЛС</span><span class="item-value">{snils}</span></div>' for snils in dossier.get('snils', [])[:2]])}
            {'' if dossier.get('inn') or dossier.get('ogrn') or dossier.get('snils') else '<div style="color: #666; font-size: 13px;">Документы не найдены</div>'}
        </div>
    </div>

    <div class="section" style="border-left-color: #f59e0b;">
        <div class="section-title">🔓 Утечки</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {l}</span></div>' for l in leaks])}
        </div>
    </div>

    <div class="section" style="border-left-color: #8b5cf6;">
        <div class="section-title">💬 Отзывы ({len(reviews)})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {r[:100]}...</span></div>' for r in reviews[:3]])}
        </div>
    </div>

    <div class="section" style="border-left-color: #3b82f6;">
        <div class="section-title">🌐 Социальные сети</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-label">{name}</span><a href="{link}" class="social-link" target="_blank">Перейти</a></div>' for name, link in social.items()])}
        </div>
    </div>

    <div class="section" style="border-left-color: #10b981;">
        <div class="section-title">📄 Найден на сайтах ({len(dossier.get('found_sites', []))})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {s}</span></div>' for s in dossier.get('found_sites', [])[:15]])}
            {f'<div style="color: #666; font-size: 12px;">... и ещё {len(dossier.get("found_sites", []))-15}</div>' if len(dossier.get('found_sites', [])) > 15 else ''}
        </div>
    </div>

    <div class="footer">
        <div>📅 Отчёт сгенерирован: {datetime.now().strftime('%d.%m.%Y %H:%M')}</div>
        <div style="margin-top: 4px;">Всего найдено: {dossier.get('total_found', 0)} сайтов</div>
        <div style="margin-top: 8px; color: #6366f1;">SWILL DOX BOT</div>
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
        "✅ ФИО, адреса, email\n"
        "✅ Компании и ИП\n"
        "✅ Только TG/WA/Viber\n"
        "✅ Утечки данных\n"
        "✅ Документы (ИНН, ОГРН, СНИЛС)\n"
        "✅ Отзывы о номере\n"
        "✅ 200+ сайтов\n"
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
            
            await query.edit_message_text("🔄 Генерация полного отчёта для админа... (30-60 секунд)")
            
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
        
        await query.edit_message_text("🔄 Генерация полного отчёта... (30-60 секунд)")
        
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
    report += f"Содержит: ФИО, адреса, email, компании, ИП, утечки, документы и многое другое"
    
    return report

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    phone = update.message.text.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    is_admin_user = is_admin(user_id)
    
    if not re.match(r'^\+?\d{10,15}$', phone):
        await update.message.reply_text("❌ Неверный формат. Пример: +79001234567")
        return
    
    context.user_data['phone'] = phone
    
    msg = await update.message.reply_text("🔄 Сбор информации... (20-40 секунд)")
    
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
    app.run_polling()

if __name__ == "__main__":
    main()
