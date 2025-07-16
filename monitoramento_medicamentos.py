#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de PDF no Google Drive (Texto e Alerta por Email)
"""

import os
import json
import logging
import requests
import hashlib
from datetime import datetime
from pathlib import Path
import smtplib
from email.mime.text import MIMEText
import sys
import PyPDF2

# CONFIGURAÇÕES
ARQUIVO_ID = '1ldltNZuBwIBfEE83mTOvzGrw_7HQEc-l'
PASTA_DADOS = 'dados_monitoramento'
ARQUIVO_ESTADO = 'estado_monitor.json'
ARQUIVO_LOG = 'log_monitor.log'
ARQUIVO_TEMP = 'temp.pdf'
PALAVRAS_CHAVE = ["Donepezil", "Memantina", "Galantamina"]

class PDFMonitor:
    def __init__(self, file_id):
        self.file_id = file_id
        self.pasta_dados = PASTA_DADOS
        self.arquivo_estado = os.path.join(self.pasta_dados, ARQUIVO_ESTADO)
        self.arquivo_log = os.path.join(self.pasta_dados, ARQUIVO_LOG)
        self.arquivo_temp = os.path.join(self.pasta_dados, ARQUIVO_TEMP)
        self.setup()

    def setup(self):
        Path(self.pasta_dados).mkdir(exist_ok=True)
        self.setup_logging()
        self.estado = self.carregar_estado()

    def setup_logging(self):
        logging.basicConfig(
            filename=self.arquivo_log,
            level=logging.INFO,
            format='%(asctime)s | %(levelname)8s | %(message)s',
            encoding='utf-8'
        )
        logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

    def carregar_estado(self):
        if os.path.exists(self.arquivo_estado):
            if os.path.getsize(self.arquivo_estado) == 0:
                logging.warning("Arquivo estado_monitor.json vazio, criando estado inicial.")
                return self.estado_inicial()
            with open(self.arquivo_estado, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return self.estado_inicial()
    
    def estado_inicial(self):
        estado = {
            "texto_ultimo": "",
            "hash_ultimo": "",
            "data_ultimo": "",
            "execucoes": 0,
            "mudancas": 0
        }
        self.salvar_estado(estado)
        return estado

    def salvar_estado(self, estado):
        estado['data_atualizacao'] = datetime.now().isoformat()
        with open(self.arquivo_estado, 'w', encoding='utf-8') as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
        logging.info("Estado salvo")

    def baixar_pdf(self):
        url = f"https://drive.google.com/uc?export=download&id={self.file_id}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            with open(self.arquivo_temp, 'wb') as f:
                f.write(r.content)
            logging.info("Download concluído")
            return True
        except Exception as e:
            logging.error(f"Erro no download: {str(e)}")
            return False

    def extrair_texto(self):
        try:
            texto = ""
            with open(self.arquivo_temp, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    t = page.extract_text()
                    if t:
                        texto += t + "\n"
            texto = ' '.join(texto.strip().split())
            logging.info(f"Texto extraído ({len(texto)} caracteres)")
            return texto
        except Exception as e:
            logging.error(f"Erro na extração: {str(e)}")
            return ""

    def calcular_hash(self, texto):
        return hashlib.md5(texto.encode('utf-8')).hexdigest() if texto else ""

    def verificar_palavras_chave(self, texto):
        encontradas = [p for p in PALAVRAS_CHAVE if p.lower() in texto.lower()]
        logging.info(f"Medicamentos em falta atualmente: {encontradas}")
        return encontradas

    def enviar_email(self, encontradas):
        remetente = os.environ["EMAIL_USUARIO"]
        senha = os.environ["EMAIL_SENHA"]
        destinatario = "edunaka@live.com"
    
        if encontradas:
            corpo = (
                "O PDF foi atualizado.\n\n"
                f"Medicamentos em falta: {', '.join(encontradas)}"
            )
        else:
            corpo = (
                "O PDF foi atualizado.\n\n"
                f"Todos os medicamentos estão disponíveis: {', '.join(PALAVRAS_CHAVE)}"
            )
        
        msg = MIMEText(corpo, "plain", "utf-8")
        msg["Subject"] = "🚨 Atualização PDF de Medicamentos"
        msg["From"] = remetente
        msg["To"] = destinatario
    
        # Aqui adiciona prioridade alta
        msg["X-Priority"] = "1"
        msg["Importance"] = "High"
    
        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(remetente, senha)
            server.sendmail(remetente, [destinatario], msg.as_string())
            server.quit()
            logging.info("E-mail enviado com prioridade alta")
        except Exception as e:
            logging.error(f"Erro ao enviar e-mail: {str(e)}")

    def executar(self):
        self.estado["execucoes"] += 1
        if not self.baixar_pdf():
            return

        texto = self.extrair_texto()
        if not texto:
            return

        hash_atual = self.calcular_hash(texto)
        if hash_atual != self.estado["hash_ultimo"]:
            self.estado["mudancas"] += 1
            self.estado["texto_ultimo"] = texto
            self.estado["hash_ultimo"] = hash_atual
            self.estado["data_ultimo"] = datetime.now().isoformat()

            encontradas = self.verificar_palavras_chave(texto)
            self.enviar_email(encontradas)
        else:
            logging.info("Sem mudanças detectadas")

        self.salvar_estado(self.estado)
        os.remove(self.arquivo_temp)

def main():
    monitor = PDFMonitor(ARQUIVO_ID)
    monitor.executar()

if __name__ == "__main__":
    main()
