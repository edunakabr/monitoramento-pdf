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
from datetime import datetime, time
from pathlib import Path
import smtplib
from email.mime.text import MIMEText
import sys
import PyPDF2
import schedule
import time as time_module
import threading

# CONFIGURAÇÕES
ARQUIVO_ID = '1ldltNZuBwIBfEE83mTOvzGrw_7HQEc-l'
PASTA_DADOS = 'dados_monitoramento'
ARQUIVO_ESTADO = 'estado_monitor.json'
ARQUIVO_LOG = 'log_monitor.log'
ARQUIVO_TEMP = 'temp.pdf'
PALAVRAS_CHAVE = ["Donepezil", "Memantina", "Galantamina"]

# CONFIGURAÇÕES DE AGENDAMENTO
SCHEDULE_CONFIG = {
    # Modo de execução: 'periodico', 'horarios_especificos' ou 'ambos'
    'modo': 'periodico',
    
    # Configurações para modo periódico
    'periodicidade_minutos': 30,  # Executar a cada X minutos
    'horario_inicio': '08:00',    # Horário para começar (formato HH:MM)
    'horario_fim': '18:00',       # Horário para parar (formato HH:MM)
    
    # Configurações para horários específicos
    'horarios_especificos': [
        '09:00',
        '12:00', 
        '15:00',
        '17:00'
    ],
    
    # Configurações gerais
    'executar_fins_semana': True,  # Se deve executar aos sábados e domingos
    'dias_semana': [0, 1, 2, 3, 4, 5, 6],  # 0=Segunda, 1=Terça... 6=Domingo
}

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
                logging.warning("Arquivo estado_monitor.json está vazio. Criando estado inicial.")
                return self.estado_inicial()
            try:
                with open(self.arquivo_estado, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Erro ao carregar estado: {e}. Criando estado inicial.")
                return self.estado_inicial()
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
        
        # Obter data e hora atual
        agora = datetime.now()
        data_hora_formatada = agora.strftime("%d/%m/%Y às %H:%M:%S")
        data_hora_subject = agora.strftime("%d/%m/%Y %H:%M")
    
        if encontradas:
            corpo = (
                f"Execução realizada em: {data_hora_formatada}\n\n"
                "O PDF foi atualizado.\n\n"
                f"Medicamentos em falta: {', '.join(encontradas)}"
            )
            # Texto resumido para o subject
            info_subject = f"Medicamentos em falta: {', '.join(encontradas)}"
        else:
            corpo = (
                f"Execução realizada em: {data_hora_formatada}\n\n"
                "O PDF foi atualizado.\n\n"
                f"Todos os medicamentos estão disponíveis: {', '.join(PALAVRAS_CHAVE)}"
            )
            # Texto resumido para o subject
            info_subject = f"Todos os medicamentos estão disponíveis: {', '.join(PALAVRAS_CHAVE)}"
        
        msg = MIMEText(corpo, "plain", "utf-8")
        msg["Subject"] = f"🚨 Atualização PDF de Medicamentos - {data_hora_subject} ({info_subject})"
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
            logging.info(f"E-mail enviado com prioridade alta em {data_hora_formatada}")
        except Exception as e:
            logging.error(f"Erro ao enviar e-mail: {str(e)}")

    def executar(self):
        self.estado["execucoes"] += 1
        logging.info(f"Iniciando execução #{self.estado['execucoes']}")
        
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

    def deve_executar_agora(self):
        """Verifica se deve executar baseado nas configurações de horário"""
        agora = datetime.now()
        
        # Verifica se é um dia da semana permitido
        if agora.weekday() not in SCHEDULE_CONFIG['dias_semana']:
            return False
            
        # Verifica se não deve executar em fins de semana
        if not SCHEDULE_CONFIG['executar_fins_semana'] and agora.weekday() >= 5:
            return False
            
        # Para modo periódico, verifica se está dentro do horário permitido
        if SCHEDULE_CONFIG['modo'] in ['periodico', 'ambos']:
            hora_inicio = datetime.strptime(SCHEDULE_CONFIG['horario_inicio'], '%H:%M').time()
            hora_fim = datetime.strptime(SCHEDULE_CONFIG['horario_fim'], '%H:%M').time()
            hora_atual = agora.time()
            
            if not (hora_inicio <= hora_atual <= hora_fim):
                return False
                
        return True

def configurar_schedule():
    """Configura o agendamento baseado nas configurações"""
    monitor = PDFMonitor(ARQUIVO_ID)
    
    def executar_com_verificacao():
        if monitor.deve_executar_agora():
            monitor.executar()
        else:
            logging.info("Execução pulada - fora do horário permitido")
    
    # Limpa agendamentos anteriores
    schedule.clear()
    
    if SCHEDULE_CONFIG['modo'] == 'periodico':
        # Modo periódico
        schedule.every(SCHEDULE_CONFIG['periodicidade_minutos']).minutes.do(executar_com_verificacao)
        logging.info(f"Agendamento periódico configurado: a cada {SCHEDULE_CONFIG['periodicidade_minutos']} minutos")
        logging.info(f"Horário de funcionamento: {SCHEDULE_CONFIG['horario_inicio']} às {SCHEDULE_CONFIG['horario_fim']}")
        
    elif SCHEDULE_CONFIG['modo'] == 'horarios_especificos':
        # Modo horários específicos
        for horario in SCHEDULE_CONFIG['horarios_especificos']:
            schedule.every().day.at(horario).do(monitor.executar)
            logging.info(f"Agendamento configurado para: {horario}")
            
    elif SCHEDULE_CONFIG['modo'] == 'ambos':
        # Modo combinado
        schedule.every(SCHEDULE_CONFIG['periodicidade_minutos']).minutes.do(executar_com_verificacao)
        for horario in SCHEDULE_CONFIG['horarios_especificos']:
            schedule.every().day.at(horario).do(monitor.executar)
        logging.info(f"Agendamento combinado configurado:")
        logging.info(f"- Periódico: a cada {SCHEDULE_CONFIG['periodicidade_minutos']} minutos ({SCHEDULE_CONFIG['horario_inicio']} às {SCHEDULE_CONFIG['horario_fim']})")
        logging.info(f"- Horários específicos: {', '.join(SCHEDULE_CONFIG['horarios_especificos'])}")
    
    # Informações sobre dias da semana
    dias_nomes = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    dias_permitidos = [dias_nomes[i] for i in SCHEDULE_CONFIG['dias_semana']]
    logging.info(f"Dias permitidos: {', '.join(dias_permitidos)}")

def executar_schedule():
    """Executa o loop principal do schedule"""
    configurar_schedule()
    
    logging.info("=== MONITOR DE PDF INICIADO ===")
    logging.info("Pressione Ctrl+C para parar")
    
    try:
        while True:
            schedule.run_pending()
            time_module.sleep(1)
    except KeyboardInterrupt:
        logging.info("Monitor interrompido pelo usuário")
    except Exception as e:
        logging.error(f"Erro no schedule: {str(e)}")

def main():
    """Função principal com opções de execução"""
    if len(sys.argv) > 1 and sys.argv[1] == '--single':
        # Execução única para testes
        logging.info("=== EXECUÇÃO ÚNICA (TESTE) ===")
        monitor = PDFMonitor(ARQUIVO_ID)
        monitor.executar()
    else:
        # Execução com schedule
        executar_schedule()

if __name__ == "__main__":
    main()



"""
# EXEMPLOS DE CONFIGURAÇÕES DE SCHEDULE

# =====================================================
# EXEMPLO 1: Execução a cada 30 minutos (8h às 18h)
# =====================================================
SCHEDULE_CONFIG = {
    'modo': 'periodico',
    'periodicidade_minutos': 30,
    'horario_inicio': '08:00',
    'horario_fim': '18:00',
    'executar_fins_semana': True,
    'dias_semana': [0, 1, 2, 3, 4, 5, 6],  # Todos os dias
}

# =====================================================
# EXEMPLO 2: Execução apenas em horários específicos
# =====================================================
SCHEDULE_CONFIG = {
    'modo': 'horarios_especificos',
    'horarios_especificos': [
        '09:00',
        '12:00', 
        '15:00',
        '17:00'
    ],
    'executar_fins_semana': False,
    'dias_semana': [0, 1, 2, 3, 4],  # Apenas dias úteis
}

# =====================================================
# EXEMPLO 3: Execução combinada (periódico + horários específicos)
# =====================================================
SCHEDULE_CONFIG = {
    'modo': 'ambos',
    'periodicidade_minutos': 60,  # A cada hora
    'horario_inicio': '09:00',
    'horario_fim': '17:00',
    'horarios_especificos': [
        '08:30',  # Execução extra antes do período
        '18:00',  # Execução extra após o período
    ],
    'executar_fins_semana': True,
    'dias_semana': [0, 1, 2, 3, 4, 5, 6],
}

# =====================================================
# EXEMPLO 4: Apenas dias úteis, execução frequente
# =====================================================
SCHEDULE_CONFIG = {
    'modo': 'periodico',
    'periodicidade_minutos': 15,  # A cada 15 minutos
    'horario_inicio': '08:00',
    'horario_fim': '20:00',
    'executar_fins_semana': False,
    'dias_semana': [0, 1, 2, 3, 4],  # Segunda a sexta
}

# =====================================================
# EXEMPLO 5: Horários específicos para monitoramento crítico
# =====================================================
SCHEDULE_CONFIG = {
    'modo': 'horarios_especificos',
    'horarios_especificos': [
        '07:00',  # Manhã cedo
        '09:00',  # Início expediente
        '12:00',  # Almoço
        '14:00',  # Pós-almoço
        '16:00',  # Tarde
        '18:00',  # Final expediente
        '20:00',  # Noite
    ],
    'executar_fins_semana': True,
    'dias_semana': [0, 1, 2, 3, 4, 5, 6],
}

# =====================================================
# CÓDIGOS DOS DIAS DA SEMANA
# =====================================================
# 0 = Segunda-feira
# 1 = Terça-feira
# 2 = Quarta-feira
# 3 = Quinta-feira
# 4 = Sexta-feira
# 5 = Sábado
# 6 = Domingo
"""
