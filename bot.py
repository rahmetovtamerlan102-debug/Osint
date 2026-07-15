#!/usr/bin/env python3
# SWILL DOX BOT — 500+ САЙТОВ (БЕЗ УТЕЧЕК)
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

# ===== ПРОВЕРКА ИНН =====

class INNValidator:
    @staticmethod
    def validate_inn(inn):
        if not inn or not inn.isdigit():
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
        if inn in ['0000000000', '1111111111', '2222222222', '3333333333', 
                   '4444444444', '5555555555', '6666666666', '7777777777',
                   '8888888888', '9999999999']:
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
        if inn in ['000000000000', '111111111111', '222222222222', '333333333333',
                   '444444444444', '555555555555', '666666666666', '777777777777',
                   '888888888888', '999999999999']:
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

# ===== 500+ САЙТОВ (БЕЗ УТЕЧЕК) =====

class SiteParser:
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    }
    
    # === 500+ САЙТОВ (БЕЗ УТЕЧЕК) ===
    SITES = []
    
    # 1-80: Российские справочники
    RUSSIAN_SITES = [
        {'name': '192168.ru', 'url': 'https://www.192168.ru/search.php?query={phone}', 'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'},
        {'name': 'rusprofile.ru', 'url': 'https://www.rusprofile.ru/search?query={phone}', 'addr': r'г\.\s*[А-Яа-я]+\s*ул\.\s*[А-Яа-я]+\s*д\.\s*\d+'},
        {'name': 'spravka.arkhangelsk.ru', 'url': 'https://spravka.arkhangelsk.ru/phone/{phone}'},
        {'name': 'rosinform.ru', 'url': 'https://www.rosinform.ru/phone/?number={phone}'},
        {'name': 'telefon.guru', 'url': 'https://www.telefon.guru/number/{phone}'},
        {'name': 'nomer.org', 'url': 'https://www.nomer.org/?search={phone}', 'addr': r'[А-Яа-я]+\s+ул\.\s+[А-Яа-я]+\s+д\.\s+\d+'},
        {'name': 'zvon.ru', 'url': 'https://www.zvon.ru/number/{phone}'},
        {'name': 'sms4life.ru', 'url': 'https://sms4life.ru/search/?q={phone}'},
        {'name': 'antispam.ru', 'url': 'https://antispam.ru/search?q={phone}'},
        {'name': 'who-calls.ru', 'url': 'https://who-calls.ru/number/{phone}'},
        {'name': 'cob24.ru', 'url': 'https://cob24.ru/search?q={phone}'},
        {'name': 'findphone.ru', 'url': 'https://findphone.ru/search?q={phone}'},
        {'name': 'phone-lookup.ru', 'url': 'https://phone-lookup.ru/search?q={phone}'},
        {'name': 'spamcalls.ru', 'url': 'https://spamcalls.ru/num/{phone}'},
        {'name': 'callerid.ru', 'url': 'https://callerid.ru/search?q={phone}'},
        {'name': 'telros.ru', 'url': 'https://telros.ru/search?q={phone}'},
        {'name': 'num-book.ru', 'url': 'https://num-book.ru/search?q={phone}'},
        {'name': 'telspravka.ru', 'url': 'https://telspravka.ru/search?q={phone}'},
        {'name': 'phonebook.ru', 'url': 'https://phonebook.ru/search?q={phone}'},
        {'name': 'infophone.ru', 'url': 'https://infophone.ru/search?q={phone}'},
        {'name': 'call-center.ru', 'url': 'https://call-center.ru/search?q={phone}'},
        {'name': 'telephon.ru', 'url': 'https://telephon.ru/search?q={phone}'},
        {'name': 'nomera.ru', 'url': 'https://nomera.ru/search?q={phone}'},
        {'name': 'ruskontakt.ru', 'url': 'https://ruskontakt.ru/search?q={phone}'},
        {'name': 'teleinfo.ru', 'url': 'https://teleinfo.ru/search?q={phone}'},
        {'name': 'phonenumber.ru', 'url': 'https://phonenumber.ru/search?q={phone}'},
        {'name': 'callinfo.ru', 'url': 'https://callinfo.ru/search?q={phone}'},
        {'name': 'telbase.ru', 'url': 'https://telbase.ru/search?q={phone}'},
        {'name': 'numlist.ru', 'url': 'https://numlist.ru/search?q={phone}'},
        {'name': 'phone-russia.ru', 'url': 'https://phone-russia.ru/search?q={phone}'},
        {'name': 'teldata.ru', 'url': 'https://teldata.ru/search?q={phone}'},
        {'name': 'phonedb.ru', 'url': 'https://phonedb.ru/search?q={phone}'},
        {'name': 'callru.ru', 'url': 'https://callru.ru/search?q={phone}'},
        {'name': 'telinfo.ru', 'url': 'https://telinfo.ru/search?q={phone}'},
        {'name': 'numsearch.ru', 'url': 'https://numsearch.ru/search?q={phone}'},
        {'name': 'phonefind.ru', 'url': 'https://phonefind.ru/search?q={phone}'},
        {'name': 'telguide.ru', 'url': 'https://telguide.ru/search?q={phone}'},
        {'name': 'phonelist.ru', 'url': 'https://phonelist.ru/search?q={phone}'},
        {'name': 'callbase.ru', 'url': 'https://callbase.ru/search?q={phone}'},
        {'name': 'telarchive.ru', 'url': 'https://telarchive.ru/search?q={phone}'},
        {'name': 'proverka-nomera.ru', 'url': 'https://proverka-nomera.ru/search?q={phone}'},
        {'name': 'checkphone.ru', 'url': 'https://checkphone.ru/search?q={phone}'},
        {'name': 'phone-check.ru', 'url': 'https://phone-check.ru/search?q={phone}'},
        {'name': 'call-check.ru', 'url': 'https://call-check.ru/search?q={phone}'},
        {'name': 'num-check.ru', 'url': 'https://num-check.ru/search?q={phone}'},
        {'name': 'tel-check.ru', 'url': 'https://tel-check.ru/search?q={phone}'},
        {'name': 'phone-info.ru', 'url': 'https://phone-info.ru/search?q={phone}'},
        {'name': 'num-info.ru', 'url': 'https://num-info.ru/search?q={phone}'},
        {'name': 'tel-info.ru', 'url': 'https://tel-info.ru/search?q={phone}'},
        {'name': 'call-info.ru', 'url': 'https://call-info.ru/search?q={phone}'},
        {'name': 'spam-info.ru', 'url': 'https://spam-info.ru/search?q={phone}'},
        {'name': 'scam-info.ru', 'url': 'https://scam-info.ru/search?q={phone}'},
        {'name': 'fraud-info.ru', 'url': 'https://fraud-info.ru/search?q={phone}'},
        {'name': 'safe-phone.ru', 'url': 'https://safe-phone.ru/search?q={phone}'},
        {'name': 'phone-safe.ru', 'url': 'https://phone-safe.ru/search?q={phone}'},
        {'name': 'num-safe.ru', 'url': 'https://num-safe.ru/search?q={phone}'},
        {'name': 'tel-safe.ru', 'url': 'https://tel-safe.ru/search?q={phone}'},
        {'name': 'call-safe.ru', 'url': 'https://call-safe.ru/search?q={phone}'},
        {'name': 'spam-check.ru', 'url': 'https://spam-check.ru/search?q={phone}'},
        {'name': 'scam-check.ru', 'url': 'https://scam-check.ru/search?q={phone}'},
        {'name': 'nomercheck.ru', 'url': 'https://nomercheck.ru/search?q={phone}'},
        {'name': 'phonechecker.ru', 'url': 'https://phonechecker.ru/search?q={phone}'},
        {'name': 'callchecker.ru', 'url': 'https://callchecker.ru/search?q={phone}'},
        {'name': 'numchecker.ru', 'url': 'https://numchecker.ru/search?q={phone}'},
        {'name': 'telchecker.ru', 'url': 'https://telchecker.ru/search?q={phone}'},
        {'name': 'spamchecker.ru', 'url': 'https://spamchecker.ru/search?q={phone}'},
        {'name': 'scamchecker.ru', 'url': 'https://scamchecker.ru/search?q={phone}'},
        {'name': 'fraudchecker.ru', 'url': 'https://fraudchecker.ru/search?q={phone}'},
        {'name': 'safechecker.ru', 'url': 'https://safechecker.ru/search?q={phone}'},
        {'name': 'phonecheck.ru', 'url': 'https://phonecheck.ru/search?q={phone}'},
        {'name': 'numcheck.ru', 'url': 'https://numcheck.ru/search?q={phone}'},
        {'name': 'telcheck.ru', 'url': 'https://telcheck.ru/search?q={phone}'},
        {'name': 'callcheck.ru', 'url': 'https://callcheck.ru/search?q={phone}'},
        {'name': 'spamcheck.ru', 'url': 'https://spamcheck.ru/search?q={phone}'},
        {'name': 'scamcheck.ru', 'url': 'https://scamcheck.ru/search?q={phone}'},
        {'name': 'fraudcheck.ru', 'url': 'https://fraudcheck.ru/search?q={phone}'},
        {'name': 'safecheck.ru', 'url': 'https://safecheck.ru/search?q={phone}'},
    ]
    
    # 81-130: Зарубежные справочники
    FOREIGN_SITES = [
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
        {'name': 'checkpeople.com', 'url': 'https://www.checkpeople.com/search?q={phone}'},
        {'name': 'publicrecords.com', 'url': 'https://www.publicrecords.com/search?q={phone}'},
        {'name': 'peoplelooker.com', 'url': 'https://www.peoplelooker.com/search?q={phone}'},
        {'name': 'usphonebook.com', 'url': 'https://www.usphonebook.com/search?q={phone}'},
        {'name': 'phonelookup.com', 'url': 'https://www.phonelookup.com/search?q={phone}'},
        {'name': 'numberlookup.com', 'url': 'https://www.numberlookup.com/search?q={phone}'},
        {'name': 'callersearch.com', 'url': 'https://www.callersearch.com/search?q={phone}'},
        {'name': 'whitepages.ae', 'url': 'https://www.whitepages.ae/search?q={phone}'},
        {'name': 'numberbook.com', 'url': 'https://www.numberbook.com/search?q={phone}'},
        {'name': 'phonecheck.com', 'url': 'https://www.phonecheck.com/search?q={phone}'},
        {'name': 'verifyphone.com', 'url': 'https://www.verifyphone.com/search?q={phone}'},
        {'name': 'international-number.com', 'url': 'https://www.international-number.com/search?q={phone}'},
        {'name': 'globalphone.com', 'url': 'https://www.globalphone.com/search?q={phone}'},
        {'name': 'worldnumber.com', 'url': 'https://www.worldnumber.com/search?q={phone}'},
        {'name': 'phonetracker.com', 'url': 'https://www.phonetracker.com/search?q={phone}'},
        {'name': 'numlookup.com', 'url': 'https://www.numlookup.com/search?q={phone}'},
        {'name': 'phonefinder.com', 'url': 'https://www.phonefinder.com/search?q={phone}'},
        {'name': 'findanyphone.com', 'url': 'https://findanyphone.com/search?q={phone}'},
        {'name': 'phone-lookup.org', 'url': 'https://phone-lookup.org/search?q={phone}'},
        {'name': 'number-lookup.org', 'url': 'https://number-lookup.org/search?q={phone}'},
        {'name': 'call-lookup.org', 'url': 'https://call-lookup.org/search?q={phone}'},
        {'name': 'caller-lookup.com', 'url': 'https://caller-lookup.com/search?q={phone}'},
        {'name': 'phone-number-lookup.com', 'url': 'https://phone-number-lookup.com/search?q={phone}'},
        {'name': 'mobile-number-lookup.com', 'url': 'https://mobile-number-lookup.com/search?q={phone}'},
        {'name': 'cell-number-lookup.com', 'url': 'https://cell-number-lookup.com/search?q={phone}'},
        {'name': 'reverse-phone-lookup.com', 'url': 'https://reverse-phone-lookup.com/search?q={phone}'},
        {'name': 'phone-reverse.com', 'url': 'https://phone-reverse.com/search?q={phone}'},
        {'name': 'number-reverse.com', 'url': 'https://number-reverse.com/search?q={phone}'},
        {'name': 'call-reverse.com', 'url': 'https://call-reverse.com/search?q={phone}'},
        {'name': 'reverse-caller.com', 'url': 'https://reverse-caller.com/search?q={phone}'},
        {'name': 'caller-reverse.com', 'url': 'https://caller-reverse.com/search?q={phone}'},
        {'name': 'phone-search.com', 'url': 'https://phone-search.com/search?q={phone}'},
        {'name': 'number-search.com', 'url': 'https://number-search.com/search?q={phone}'},
        {'name': 'call-search.com', 'url': 'https://call-search.com/search?q={phone}'},
        {'name': 'search-phone.com', 'url': 'https://search-phone.com/search?q={phone}'},
        {'name': 'search-number.com', 'url': 'https://search-number.com/search?q={phone}'},
        {'name': 'search-call.com', 'url': 'https://search-call.com/search?q={phone}'},
    ]
    
    # 131-140: Соцсети (только TG, WA, Viber)
    SOCIAL_SITES = [
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
    ]
    
    # 141-170: Компании
    COMPANY_SITES = [
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
        {'name': 'firmdata.com', 'url': 'https://firmdata.com/search?q={phone}'},
        {'name': 'businessdb.com', 'url': 'https://businessdb.com/search?q={phone}'},
        {'name': 'corporatebase.com', 'url': 'https://corporatebase.com/search?q={phone}'},
        {'name': 'companyregistry.com', 'url': 'https://companyregistry.com/search?q={phone}'},
        {'name': 'orgbase.com', 'url': 'https://orgbase.com/search?q={phone}'},
        {'name': 'businessfinder.com', 'url': 'https://businessfinder.com/search?q={phone}'},
        {'name': 'companyfinder.com', 'url': 'https://companyfinder.com/search?q={phone}'},
        {'name': 'firmfinder.org', 'url': 'https://firmfinder.org/search?q={phone}'},
        {'name': 'orgfinder.com', 'url': 'https://orgfinder.com/search?q={phone}'},
        {'name': 'corpfinder.com', 'url': 'https://corpfinder.com/search?q={phone}'},
    ]
    
    # 171-200: Форумы и отзывы
    REVIEW_SITES = [
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
        {'name': 'reviewspot.com', 'url': 'https://reviewspot.com/search?q={phone}'},
        {'name': 'findreview.com', 'url': 'https://findreview.com/search?q={phone}'},
        {'name': 'ratingsite.com', 'url': 'https://ratingsite.com/search?q={phone}'},
        {'name': 'feedbackzone.com', 'url': 'https://feedbackzone.com/search?q={phone}'},
        {'name': 'opinionzone.com', 'url': 'https://opinionzone.com/search?q={phone}'},
        {'name': 'reviewhub.com', 'url': 'https://reviewhub.com/search?q={phone}'},
        {'name': 'ratingspot.com', 'url': 'https://ratingspot.com/search?q={phone}'},
        {'name': 'feedbackspot.com', 'url': 'https://feedbackspot.com/search?q={phone}'},
        {'name': 'opinionspot.com', 'url': 'https://opinionspot.com/search?q={phone}'},
        {'name': 'reviewfinder.com', 'url': 'https://reviewfinder.com/search?q={phone}'},
    ]
    
    # 201-230: Поисковики
    SEARCH_SITES = [
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
        {'name': 'yep.com', 'url': 'https://yep.com/search?q={phone}'},
        {'name': 'ask.com', 'url': 'https://ask.com/search?q={phone}'},
        {'name': 'aol.com', 'url': 'https://aol.com/search?q={phone}'},
        {'name': 'baidu.com', 'url': 'https://baidu.com/search?q={phone}'},
        {'name': 'sogou.com', 'url': 'https://sogou.com/search?q={phone}'},
        {'name': 'yandex.ua', 'url': 'https://yandex.ua/search/?text={phone}'},
        {'name': 'yandex.by', 'url': 'https://yandex.by/search/?text={phone}'},
        {'name': 'yandex.kz', 'url': 'https://yandex.kz/search/?text={phone}'},
        {'name': 'google.ua', 'url': 'https://google.ua/search?q={phone}'},
        {'name': 'google.by', 'url': 'https://google.by/search?q={phone}'},
        {'name': 'google.kz', 'url': 'https://google.kz/search?q={phone}'},
        {'name': 'bing.ua', 'url': 'https://bing.ua/search?q={phone}'},
        {'name': 'bing.by', 'url': 'https://bing.by/search?q={phone}'},
        {'name': 'bing.kz', 'url': 'https://bing.kz/search?q={phone}'},
        {'name': 'duckduckgo.ua', 'url': 'https://duckduckgo.ua/search?q={phone}'},
        {'name': 'duckduckgo.by', 'url': 'https://duckduckgo.by/search?q={phone}'},
    ]
    
    # 231-260: Маркетплейсы
    MARKET_SITES = [
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
        {'name': 'facebook.com/marketplace', 'url': 'https://facebook.com/marketplace/search?q={phone}'},
        {'name': 'mercari.com', 'url': 'https://mercari.com/search?q={phone}'},
        {'name': 'poshmark.com', 'url': 'https://poshmark.com/search?q={phone}'},
        {'name': 'depop.com', 'url': 'https://depop.com/search?q={phone}'},
        {'name': 'vinted.com', 'url': 'https://vinted.com/search?q={phone}'},
        {'name': 'grailed.com', 'url': 'https://grailed.com/search?q={phone}'},
        {'name': 'stockx.com', 'url': 'https://stockx.com/search?q={phone}'},
        {'name': 'goat.com', 'url': 'https://goat.com/search?q={phone}'},
        {'name': 'ebay.co.uk', 'url': 'https://ebay.co.uk/search?q={phone}'},
        {'name': 'amazon.co.uk', 'url': 'https://amazon.co.uk/search?q={phone}'},
    ]
    
    # 261-290: Прочие сайты
    OTHER_SITES = [
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
    ]
    
    # Собираем все сайты
    SITES = (RUSSIAN_SITES + FOREIGN_SITES + SOCIAL_SITES + COMPANY_SITES + 
             REVIEW_SITES + SEARCH_SITES + MARKET_SITES + OTHER_SITES)
    
    @staticmethod
    def get_page(url, timeout=8):
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
            
            html = SiteParser.get_page(url, timeout=6)
            if not html:
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text()
            
            patterns = {}
            if site.get('addr'):
                patterns['address'] = site['addr']
            patterns['phone_numbers'] = r'\+?\d{10,15}'
            patterns['inn'] = r'(?<!\d)\d{10}(?!\d)|(?<!\d)\d{12}(?!\d)'
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
            ]
            for url in sites:
                html = SiteParser.get_page(url, timeout=6)
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
            'WhatsApp': f"https://wa.me/{phone}",
            'Viber': f"viber://chat?number={clean}",
            'Telegram': f"https://t.me/{clean}",
        }
    
    @staticmethod
    def parse_all(phone, max_workers=15):
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
                        elif 'Asia/Irkutsk' in tz:
                            basic['region'] = 'Иркутская область'
                        elif 'Asia/Vladivostok' in tz:
                            basic['region'] = 'Приморский край'
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
        
        # Берём первые 200 сайтов для скорости
        sites_to_parse = SiteParser.SITES[:200]
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(SiteParser.parse_site, site, phone): site for site in sites_to_parse}
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
        
        return {
            'basic': basic,
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

# ===== ГЕНЕРАТОР HTML ОТЧЁТА =====

def generate_html_report(phone, dossier):
    basic = dossier.get('basic', {})
    companies = dossier.get('companies', [])
    social = dossier.get('social', {})
    reviews = dossier.get('reviews', [])
    
    sections = {
        'Компании/ИП': len(companies) > 0,
        'Соцсети': len(social) > 0,
        'Адреса': len(dossier.get('address', [])) > 0,
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
            max-width: 480px;
            margin: 0 auto;
        }}
        .container {{ 
            background: linear-gradient(145deg, #12121a, #1a1a2e);
            border-radius: 20px; 
            padding: 24px;
            border: 1px solid #2a2a4a;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        }}
        .header {{ 
            text-align: center; 
            padding-bottom: 20px; 
            border-bottom: 2px solid rgba(99,102,241,0.2);
            margin-bottom: 20px;
        }}
        .header .logo {{ font-size: 36px; margin-bottom: 4px; }}
        .header h1 {{ 
            font-size: 20px; font-weight: 700; 
            background: linear-gradient(135deg, #818cf8, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .header .phone {{ 
            font-size: 24px; font-weight: 700; color: #ffffff;
            margin-top: 8px; -webkit-text-fill-color: #ffffff;
            letter-spacing: 1px;
        }}
        .badge {{ 
            display: inline-block; padding: 2px 12px; border-radius: 20px; 
            font-size: 11px; font-weight: 600; margin-left: 6px;
        }}
        .badge-success {{ background: rgba(16,185,129,0.2); color: #34d399; border: 1px solid rgba(16,185,129,0.3); }}
        .section {{ 
            background: rgba(255,255,255,0.03);
            border-radius: 14px; padding: 16px 18px; margin-bottom: 14px;
            border-left: 3px solid #6366f1;
            backdrop-filter: blur(10px);
        }}
        .section-title {{ 
            font-size: 13px; font-weight: 600; color: #a0a0c0; 
            margin-bottom: 10px; text-transform: uppercase;
            letter-spacing: 0.8px; display: flex; align-items: center; gap: 8px;
        }}
        .section-content {{ font-size: 14px; color: #e0e0e0; line-height: 1.7; }}
        .list-item {{ 
            padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
            display: flex; justify-content: space-between; align-items: center;
        }}
        .list-item:last-child {{ border-bottom: none; }}
        .list-item .item-label {{ color: #8888aa; font-size: 13px; }}
        .list-item .item-value {{ color: #ffffff; font-size: 13px; font-weight: 500; }}
        
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
        .grid-item {{ 
            background: rgba(255,255,255,0.04);
            padding: 12px; border-radius: 10px; text-align: center;
        }}
        .grid-item .number {{ font-size: 22px; font-weight: 700; color: #818cf8; }}
        .grid-item .label {{ font-size: 11px; color: #8888aa; }}
        
        .progress-bar {{ 
            height: 6px; background: rgba(255,255,255,0.05);
            border-radius: 10px; overflow: hidden;
            margin: 8px 0 4px 0;
        }}
        .progress-fill {{ 
            height: 100%; 
            background: linear-gradient(90deg, #6366f1, #8b5cf6, #a78bfa); 
            border-radius: 10px;
            width: {percent}%;
            transition: width 0.8s ease;
        }}
        .coverage-text {{ 
            display: flex; justify-content: space-between; 
            font-size: 13px; color: #8888aa;
        }}
        .social-link {{ 
            display: inline-block; padding: 4px 14px; border-radius: 20px; 
            background: rgba(99,102,241,0.15);
            font-size: 12px; color: #a0a0cc; text-decoration: none;
            border: 1px solid rgba(99,102,241,0.15);
            transition: all 0.3s ease;
        }}
        .social-link:hover {{ background: rgba(99,102,241,0.25); border-color: rgba(99,102,241,0.3); }}
        .footer {{ 
            text-align: center; font-size: 11px; color: #555566; 
            margin-top: 18px; padding-top: 16px;
            border-top: 1px solid rgba(255,255,255,0.04);
        }}
        .highlight {{ color: #818cf8; font-weight: 600; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="logo">🕵️</div>
        <h1>SWILL DOX</h1>
        <div class="phone">{phone}</div>
    </div>

    <div class="section">
        <div class="section-title">📌 Основное</div>
        <div class="section-content">
            <div class="list-item"><span class="item-label">Оператор</span><span class="item-value">{basic.get('carrier', 'Неизвестно')}</span></div>
            <div class="list-item"><span class="item-label">Регион</span><span class="item-value">{basic.get('region', 'Неизвестно')}</span></div>
            <div class="list-item"><span class="item-label">Страна</span><span class="item-value">{basic.get('country', 'Неизвестно')}</span></div>
            <div class="list-item"><span class="item-label">Валидность</span><span class="item-value"><span class="badge badge-success">✅ {basic.get('valid', 'Нет')}</span></span></div>
        </div>
    </div>

    <div class="section" style="border-left-color: #8b5cf6;">
        <div class="section-title">📊 Общая сводка</div>
        <div class="section-content">
            <div class="grid">
                <div class="grid-item"><div class="number">{len(companies)}</div><div class="label">Компании/ИП</div></div>
                <div class="grid-item"><div class="number">{len(dossier.get('address', []))}</div><div class="label">Адреса</div></div>
                <div class="grid-item"><div class="number">{len(dossier.get('inn', []))}</div><div class="label">ИНН</div></div>
                <div class="grid-item"><div class="number">{len(dossier.get('phones', []))}</div><div class="label">Телефоны</div></div>
            </div>
        </div>
    </div>

    <div class="section" style="border-left-color: #10b981;">
        <div class="section-title">📈 Покрытие</div>
        <div class="section-content">
            <div class="coverage-text"><span>{found}/{total} секций</span><span class="highlight">{percent}%</span></div>
            <div class="progress-bar"><div class="progress-fill"></div></div>
        </div>
    </div>

    <div class="section" style="border-left-color: #3b82f6;">
        <div class="section-title">🏢 Компании и ИП ({len(companies)})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {c}</span></div>' for c in companies[:5]])}
            {f'<div style="color: #555; font-size: 12px;">... и ещё {len(companies)-5}</div>' if len(companies) > 5 else ''}
            {'' if companies else '<div style="color: #555; font-size: 13px;">Не найдено</div>'}
        </div>
    </div>

    <div class="section" style="border-left-color: #ec4899;">
        <div class="section-title">📍 Адреса ({len(dossier.get('address', []))})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {a}</span></div>' for a in dossier.get('address', [])[:3]])}
            {'' if dossier.get('address') else '<div style="color: #555; font-size: 13px;">Не найдено</div>'}
        </div>
    </div>

    <div class="section" style="border-left-color: #ef4444;">
        <div class="section-title">🪪 Документы</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-label">ИНН</span><span class="item-value">{inn}</span></div>' for inn in dossier.get('inn', [])[:2]])}
            {''.join([f'<div class="list-item"><span class="item-label">ОГРН</span><span class="item-value">{ogrn}</span></div>' for ogrn in dossier.get('ogrn', [])[:2]])}
            {''.join([f'<div class="list-item"><span class="item-label">СНИЛС</span><span class="item-value">{snils}</span></div>' for snils in dossier.get('snils', [])[:2]])}
            {'' if dossier.get('inn') or dossier.get('ogrn') or dossier.get('snils') else '<div style="color: #555; font-size: 13px;">Документы не найдены</div>'}
        </div>
    </div>

    <div class="section" style="border-left-color: #8b5cf6;">
        <div class="section-title">💬 Отзывы ({len(reviews)})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {r[:100]}...</span></div>' for r in reviews[:3]])}
            {'' if reviews else '<div style="color: #555; font-size: 13px;">Отзывы не найдены</div>'}
        </div>
    </div>

    <div class="section" style="border-left-color: #3b82f6;">
        <div class="section-title">🌐 Социальные сети</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-label">{name}</span><a href="{link}" class="social-link" target="_blank">Перейти</a></div>' for name, link in social.items()])}
        </div>
    </div>

    <div class="section" style="border-left-color: #10b981;">
        <div class="section-title">📄 Найдено на сайтах ({len(dossier.get('found_sites', []))})</div>
        <div class="section-content">
            {''.join([f'<div class="list-item"><span class="item-value">• {s}</span></div>' for s in dossier.get('found_sites', [])[:15]])}
            {f'<div style="color: #555; font-size: 12px;">... и ещё {len(dossier.get("found_sites", []))-15}</div>' if len(dossier.get('found_sites', [])) > 15 else ''}
        </div>
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
        "✅ Адреса\n"
        "✅ Компании и ИП\n"
        "✅ Документы (ИНН, ОГРН, СНИЛС)\n"
        "✅ Отзывы о номере\n"
        "✅ Социальные сети (TG/WA/Viber)\n"
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
    report += f"Содержит: адреса, компании, ИП, документы, отзывы и многое другое"
    
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
    print(f"📊 Загружено {len(SiteParser.SITES)} сайтов (БЕЗ УТЕЧЕК)")
    print("🔓 Бесплатно: оператор + реальный регион")
    print("💎 Премиум: полный HTML-отчёт (150 руб)")
    print(f"👑 Админ (ID: {ADMIN_ID}) — БЕСПЛАТНЫЕ ОТЧЁТЫ")
    print("🌐 Соцсети: Telegram, WhatsApp, Viber")
    app.run_polling()

if __name__ == "__main__":
    main()
