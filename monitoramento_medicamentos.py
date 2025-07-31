#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced PDF Medication Monitor for Google Drive
Features:
- Improved error handling and retry logic
- Better PDF text extraction with fallback methods
- Enhanced email formatting with HTML support
- More robust state management
- Better logging and monitoring
"""

import os
import json
import logging
import requests
import hashlib
import time
from datetime import datetime
from pathlib import Path
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sys
import schedule
import re
from pytz import timezone

# Try multiple PDF libraries for better compatibility
try:
    import PyPDF2
    PDF_LIBRARY = "PyPDF2"
except ImportError:
    try:
        import pdfplumber
        PDF_LIBRARY = "pdfplumber"
    except ImportError:
        try:
            import fitz  # PyMuPDF
            PDF_LIBRARY = "PyMuPDF"
        except ImportError:
            PDF_LIBRARY = None

# CONFIGURAÇÕES
ARQUIVO_ID = os.environ.get("GOOGLE_DRIVE_FILE_ID", "1ldltNZuBwIBfEE83mTOvzGrw_7HQEc-l")
PASTA_DADOS = os.environ.get("DATA_DIR", "dados_monitoramento")
ARQUIVO_ESTADO = os.path.join(PASTA_DADOS, os.environ.get("STATE_FILE", "estado_monitor.json"))
ARQUIVO_LOG = os.path.join(PASTA_DADOS, os.environ.get("LOG_FILE", "log_monitor.log"))
ARQUIVO_TEMP = os.path.join(PASTA_DADOS, os.environ.get("TEMP_PDF_FILE", "temp.pdf"))
PALAVRAS_CHAVE = ["Donepezil", "Memantina", "Galantamina"]

# CONFIGURAÇÕES DE RETRY
RETRY_CONFIG = {
    'max_retries': 3,
    'retry_delay': 5,  # segundos
    'timeout': 30,
}

# CONFIGURAÇÕES DE AGENDAMENTO
SCHEDULE_CONFIG = {
    'modo': 'horarios_especificos',
    'horarios_especificos': [
        '05:00',
        '06:00',
        '07:00',
        '08:00',
        '09:00',
        '10:00',
        '11:00',
        '12:00',
        '13:00',
        '14:00',
        '23:00',
    ],
    'executar_fins_semana': False,
    'dias_semana': [0, 1, 2, 3, 4],  # Segunda a sexta (0=Segunda, 6=Domingo)
}

# CONFIGURAÇÃO DE TIMEZONE
TIMEZONE = os.environ.get("TIMEZONE", "America/Sao_Paulo")

def get_current_datetime_in_timezone():
    """Retorna a data e hora atual no timezone configurado."""
    tz = timezone(TIMEZONE)
    return datetime.now(tz)

class PDFMonitor:
    def __init__(self, file_id: str):
        self.file_id = file_id
        self.pasta_dados = PASTA_DADOS
        self.arquivo_estado = ARQUIVO_ESTADO
        self.arquivo_log = ARQUIVO_LOG
        self.arquivo_temp = ARQUIVO_TEMP
        self.setup()

    def setup(self):
        """Inicializa o monitor"""
        Path(self.pasta_dados).mkdir(exist_ok=True)
        self.setup_logging()
        self.estado = self.carregar_estado()
        self.log_system_info()

    def setup_logging(self):
        """Configura o sistema de logging"""
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s | %(levelname)8s | %(message)s',
            handlers=[
                logging.FileHandler(self.arquivo_log, encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )

    def log_system_info(self):
        """Registra informações do sistema"""
        logging.info("=== SISTEMA DE MONITORAMENTO INICIADO ===")
        logging.info(f"Biblioteca PDF: {PDF_LIBRARY}")
        logging.info(f"Python: {sys.version.splitlines()[0]}")
        logging.info(f"Arquivo ID: {self.file_id}")
        logging.info(f"Medicamentos monitorados: {', '.join(PALAVRAS_CHAVE)}")
        logging.info(f"Timezone configurado: {TIMEZONE}")

    def carregar_estado(self) -> dict:
        """Carrega o estado anterior ou cria um novo"""
        if os.path.exists(self.arquivo_estado) and os.path.getsize(self.arquivo_estado) > 0:
            try:
                with open(self.arquivo_estado, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        logging.warning("Arquivo de estado vazio. Criando estado inicial.")
                        return self.estado_inicial()
                    estado_carregado = json.loads(content)
                    logging.info(f"Estado carregado com sucesso. Execução #{estado_carregado.get('execucoes', 0)}")
                    return estado_carregado
            except (json.JSONDecodeError, Exception) as e:
                logging.error(f"Erro ao carregar estado: {e}. O arquivo pode estar corrompido. Criando estado inicial.")
                return self.estado_inicial()
        else:
            logging.info("Primeiro uso ou arquivo de estado inexistente. Criando estado inicial.")
            return self.estado_inicial()
    
    def estado_inicial(self) -> dict:
        """Cria o estado inicial"""
        estado = {
            "texto_ultimo": "",
            "hash_ultimo": "",
            "data_ultimo": "",
            "execucoes": 0,
            "mudancas": 0,
            "ultima_mudanca": "",
            "historico_status": [],
            "erros_consecutivos": 0,
            "ultima_execucao_sucesso": ""
        }
        return estado

    def salvar_estado(self, estado: dict):
        """Salva o estado atual"""
        estado['data_atualizacao'] = get_current_datetime_in_timezone().isoformat()
        try:
            temp_arquivo_estado = self.arquivo_estado + ".tmp"
            with open(temp_arquivo_estado, 'w', encoding='utf-8') as f:
                json.dump(estado, f, ensure_ascii=False, indent=2)
            os.replace(temp_arquivo_estado, self.arquivo_estado)
            logging.info("Estado salvo com sucesso")
        except Exception as e:
            logging.error(f"Erro ao salvar estado: {e}")

    def baixar_pdf_com_retry(self) -> bool:
        """Baixa o PDF com retry automático"""
        for tentativa in range(RETRY_CONFIG['max_retries']):
            try:
                logging.info(f"Tentativa {tentativa + 1} de download...")
                
                urls_tentativa = [
                    f"https://drive.google.com/uc?export=download&id={self.file_id}",
                    f"https://docs.google.com/document/d/{self.file_id}/export?format=pdf"
                ]
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                
                for url in urls_tentativa:
                    try:
                        response = requests.get(
                            url, 
                            headers=headers, 
                            timeout=RETRY_CONFIG['timeout'],
                            stream=True
                        )
                        response.raise_for_status()
                        
                        content_type = response.headers.get('content-type', '')
                        if 'pdf' not in content_type.lower():
                            logging.warning(f"Conteúdo pode não ser PDF: {content_type} da URL {url}")
                        
                        with open(self.arquivo_temp, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        
                        if os.path.getsize(self.arquivo_temp) > 0:
                            logging.info(f"Download concluído ({os.path.getsize(self.arquivo_temp)} bytes) da URL {url}")
                            return True
                        else:
                            logging.error(f"Arquivo PDF está vazio após download da URL {url}")
                            
                    except requests.exceptions.RequestException as e:
                        logging.warning(f"Erro na URL {url}: {e}")
                        continue
                
                if tentativa < RETRY_CONFIG['max_retries'] - 1:
                    logging.info(f"Aguardando {RETRY_CONFIG['retry_delay']} segundos antes da próxima tentativa...")
                    time.sleep(RETRY_CONFIG['retry_delay'])
                
            except Exception as e:
                logging.error(f"Erro geral no download (tentativa {tentativa + 1}): {e}")
                if tentativa < RETRY_CONFIG['max_retries'] - 1:
                    time.sleep(RETRY_CONFIG['retry_delay'])
        
        logging.error("Falha no download após todas as tentativas")
        return False

    def extrair_texto_multiplas_bibliotecas(self) -> str:
        """Extrai texto usando múltiplas bibliotecas como fallback"""
        texto = ""
        
        # Tentativa 1: PyPDF2
        if PDF_LIBRARY == "PyPDF2":
            try:
                with open(self.arquivo_temp, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            texto += page_text + "\n"
                if texto.strip():
                    logging.info(f"Texto extraído com PyPDF2 ({len(texto)} caracteres)")
                    return self.limpar_texto(texto)
            except Exception as e:
                logging.warning(f"Erro com PyPDF2: {e}. Tentando outra biblioteca.")
        
        # Tentativa 2: pdfplumber
        try:
            import pdfplumber
            with pdfplumber.open(self.arquivo_temp) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        texto += page_text + "\n"
            if texto.strip():
                logging.info(f"Texto extraído com pdfplumber ({len(texto)} caracteres)")
                return self.limpar_texto(texto)
        except (ImportError, Exception) as e:
            logging.warning(f"pdfplumber não disponível ou erro: {e}. Tentando outra biblioteca.")
        
        # Tentativa 3: PyMuPDF
        try:
            import fitz
            pdf_document = fitz.open(self.arquivo_temp)
            for page_num in range(len(pdf_document)):
                page = pdf_document.load_page(page_num)
                page_text = page.get_text()
                if page_text:
                    texto += page_text + "\n"
            pdf_document.close()
            if texto.strip():
                logging.info(f"Texto extraído com PyMuPDF ({len(texto)} caracteres)")
                return self.limpar_texto(texto)
        except (ImportError, Exception) as e:
            logging.warning(f"PyMuPDF não disponível ou erro: {e}. Nenhuma outra biblioteca para tentar.")
        
        logging.error("Falha na extração de texto com todas as bibliotecas")
        return ""

    def limpar_texto(self, texto: str) -> str:
        """Limpa e normaliza o texto extraído"""
        if not texto:
            return ""
        
        # Remove caracteres de controle e normaliza espaços
        texto = re.sub(r'\s+', ' ', texto.strip())
        
        # Remove caracteres especiais problemáticos que podem vir da extração de PDF
        # Mantém letras, números, espaços e pontuações básicas
        texto = re.sub(r'[^\w\s\.,;!?-]', '', texto, flags=re.UNICODE)
        
        return texto

    def calcular_hash(self, texto: str) -> str:
        """Calcula hash MD5 do texto"""
        return hashlib.md5(texto.encode('utf-8')).hexdigest() if texto else ""

    def verificar_palavras_chave(self, texto: str) -> list[str]:
        """
        Verifica medicamentos em falta e disponíveis.
        LÓGICA INVERTIDA:
        - Medicamentos EM FALTA: encontrados no texto do PDF
        - Medicamentos DISPONÍVEIS: NÃO encontrados no texto do PDF
        """
        texto_lower = texto.lower()
        medicamentos_em_falta = []     # Encontrados no PDF (estão em falta)
        medicamentos_disponiveis = []  # NÃO encontrados no PDF (não estão em falta)
        
        for medicamento in PALAVRAS_CHAVE:
            if medicamento.lower() in texto_lower:
                medicamentos_em_falta.append(medicamento)
            else:
                medicamentos_disponiveis.append(medicamento)
        
        logging.info(f"Medicamentos EM FALTA (encontrados no PDF): {', '.join(medicamentos_em_falta) if medicamentos_em_falta else 'Nenhum'}")
        logging.info(f"Medicamentos DISPONÍVEIS (NÃO encontrados no PDF): {', '.join(medicamentos_disponiveis) if medicamentos_disponiveis else 'Nenhum'}")
        
        return medicamentos_em_falta, medicamentos_disponiveis

    def criar_email_html(self, medicamentos_em_falta: list[str], medicamentos_disponiveis: list[str]) -> str:
        """Cria conteúdo HTML para o email"""
        agora = get_current_datetime_in_timezone()
        data_hora = agora.strftime("%d/%m/%Y às %H:%M:%S")
        
        # Cor baseada no status
        cor_status = "#dc3545" if medicamentos_em_falta else "#28a745"  # vermelho se há falta, verde se todos disponíveis
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f4f4f4; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
                .header {{ background-color: {cor_status}; color: white; padding: 15px; border-radius: 5px 5px 0 0; text-align: center; }}
                .content {{ padding: 20px; border: 1px solid #eee; border-top: none; border-radius: 0 0 5px 5px; margin-top: 0; }}
                h3 {{ color: #333; border-bottom: 1px solid #eee; padding-bottom: 10px; margin-top: 0; }}
                .medicamento {{ padding: 8px 12px; margin: 8px 0; border-radius: 5px; font-size: 0.95em; display: flex; align-items: center; }}
                .falta {{ background-color: #fcecec; color: #c0392b; border: 1px solid #f5b7b1; }}
                .disponivel {{ background-color: #e6f7ed; color: #27ae60; border: 1px solid #a9dfbf; }}
                .info {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; border: 1px solid #e9ecef; }}
                .stats {{ font-size: 0.85em; color: #555; line-height: 1.6; }}
                .footer {{ text-align: center; margin-top: 20px; font-size: 0.8em; color: #888; }}
                .icon {{ margin-right: 10px; font-size: 1.2em; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>{ '🚨' if medicamentos_em_falta else '✅' } Atualização de Medicamentos - {data_hora}</h2>
                </div>
                
                <div class="content">
                    <h3>Status dos Medicamentos:</h3>
                    
                    {f'''
                    <h4 style="color: #dc3545;"><span class="icon">❌</span> Medicamentos em Falta:</h4>
                    {'' .join(f'<div class="medicamento falta"><span class="icon">•</span> {med}</div>' for med in medicamentos_em_falta)}
                    ''' if medicamentos_em_falta else ''}
                    
                    {f'''
                    <h4 style="color: #28a745;"><span class="icon">✔️</span> Medicamentos Disponíveis:</h4>
                    {'' .join(f'<div class="medicamento disponivel"><span class="icon">•</span> {med}</div>' for med in medicamentos_disponiveis)}
                    ''' if medicamentos_disponiveis else ''}
                    
                    <div class="info">
                        <h4>Informações da Execução:</h4>
                        <div class="stats">
                            <p><strong>Data/Hora da Verificação:</strong> {data_hora}</p>
                            <p><strong>Execução #:</strong> {self.estado.get('execucoes', 0)}</p>
                            <p><strong>Total de mudanças detectadas:</strong> {self.estado.get('mudancas', 0)}</p>
                            <p><strong>Hash do conteúdo atual:</strong> {self.estado.get('hash_ultimo', '')[:10]}...</p>
                            <p><strong>Última mudança registrada:</strong> {self.estado.get('ultima_mudanca', 'N/A')}</p>
                        </div>
                    </div>
                </div>
                <div class="footer">
                    Este é um e-mail automático do sistema de monitoramento de PDF.
                </div>
            </div>
        </body>
        </html>
        """
        
        return html

    def enviar_email_melhorado(self, medicamentos_em_falta: list[str], medicamentos_disponiveis: list[str]):
        """Envia email com formatação HTML melhorada"""
        try:
            remetente = os.environ.get("EMAIL_REMETENTE", "edunaka@live.com")
            destinatario = os.environ.get("EMAIL_DESTINATARIO", "edunaka@live.com")
            servidor = os.environ.get("SMTP_SERVER", "in-v3.mailjet.com")
            usuario = os.environ.get("EMAIL_USUARIO")
            senha = os.environ.get("EMAIL_SENHA")
            
            if not usuario or not senha:
                logging.error("Variáveis de ambiente EMAIL_USUARIO ou EMAIL_SENHA não configuradas. Não é possível enviar e-mail.")
                return

            agora = get_current_datetime_in_timezone()
            data_hora_subject = agora.strftime("%d/%m/%Y %H:%M")
            
            msg = MIMEMultipart('alternative')
            
            if medicamentos_em_falta:
                status_emoji = "🚨"
                status_text = f"FALTA: {', '.join(medicamentos_em_falta)}"
            else:
                status_emoji = "✅"
                status_text = "TODOS DISPONÍVEIS"
            
            msg["Subject"] = f"{status_emoji} Medicamentos - {data_hora_subject} ({status_text})"
            msg["From"] = remetente
            msg["To"] = destinatario
            
            msg["X-Priority"] = "1"
            msg["Importance"] = "High"
            msg["X-MSMail-Priority"] = "High"
            
            texto_simples = f"""
Execução realizada em: {agora.strftime("%d/%m/%Y às %H:%M:%S")}

Status do PDF:

Medicamentos em falta: {', '.join(medicamentos_em_falta) if medicamentos_em_falta else 'Nenhum'}
Medicamentos disponíveis: {', '.join(medicamentos_disponiveis) if medicamentos_disponiveis else 'Nenhum'}

Execução #{self.estado.get('execucoes', 0)} | Total de mudanças: {self.estado.get('mudancas', 0)}
            """
            
            html_content = self.criar_email_html(medicamentos_em_falta, medicamentos_disponiveis)
            
            msg.attach(MIMEText(texto_simples, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            server = smtplib.SMTP(servidor, 587)
            server.starttls()
            server.login(usuario, senha)
            server.sendmail(remetente, [destinatario], msg.as_string())
            server.quit()
            
            logging.info(f"Email enviado com sucesso: {status_text}")
            
        except Exception as e:
            logging.error(f"Erro ao enviar email: {e}")

def enviar_telegram(self, mensagem: str):
    """Envia notificação via Telegram"""
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        
        if not token or not chat_id:
            logging.error("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados.")
            return
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": mensagem
        }
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
        logging.info("✅ Notificação enviada via Telegram.")
    except Exception as e:
        logging.error(f"❌ Erro ao enviar mensagem via Telegram: {e}")

    def atualizar_historico_status(self, medicamentos_em_falta: list[str]):
        """Atualiza histórico de status dos medicamentos"""
        status_atual = {
            'timestamp': get_current_datetime_in_timezone().isoformat(),
            'medicamentos_em_falta': medicamentos_em_falta,
            'total_em_falta': len(medicamentos_em_falta)
        }
        
        if 'historico_status' not in self.estado:
            self.estado['historico_status'] = []
        
        self.estado['historico_status'].append(status_atual)
        self.estado['historico_status'] = self.estado['historico_status'][-50:]  # Mantém os últimos 50 registros

    def executar(self):
        """Executa o monitoramento principal"""
        # IMPORTANTE: Incrementar o contador ANTES de qualquer operação
        self.estado["execucoes"] = self.estado.get("execucoes", 0) + 1
        inicio = get_current_datetime_in_timezone()
        
        logging.info(f"=== EXECUÇÃO #{self.estado['execucoes']} INICIADA ===")
        
        try:
            if not self.baixar_pdf_com_retry():
                self.estado["erros_consecutivos"] = self.estado.get("erros_consecutivos", 0) + 1
                logging.error(f"Erro consecutivo #{self.estado['erros_consecutivos']}: Falha no download do PDF.")
                return
            
            texto = self.extrair_texto_multiplas_bibliotecas()
            if not texto:
                self.estado["erros_consecutivos"] = self.estado.get("erros_consecutivos", 0) + 1
                logging.error(f"Erro consecutivo #{self.estado['erros_consecutivos']}: Falha na extração de texto do PDF.")
                return
            
            hash_atual = self.calcular_hash(texto)
            mudou_conteudo = hash_atual != self.estado.get("hash_ultimo", "")
            
            medicamentos_em_falta, medicamentos_disponiveis = self.verificar_palavras_chave(texto)
            
            # Verifica se houve mudança no status dos medicamentos
            medicamentos_em_falta_anterior = self.estado.get('medicamentos_em_falta_ultimo', [])
            mudou_status_medicamentos = set(medicamentos_em_falta) != set(medicamentos_em_falta_anterior)
            
            # Condição para enviar e-mail: mudança no conteúdo do PDF OU mudança no status dos medicamentos OU force_email
            deve_enviar_notificacao = mudou_conteudo or mudou_status_medicamentos or os.environ.get("FORCE_EMAIL", "false").lower() == "true"
            
            if deve_enviar_notificacao:
                if mudou_conteudo:
                    self.estado["mudancas"] = self.estado.get("mudancas", 0) + 1
                    self.estado["texto_ultimo"] = texto
                    self.estado["hash_ultimo"] = hash_atual
                    self.estado["data_ultimo"] = get_current_datetime_in_timezone().isoformat()
                    self.estado["ultima_mudanca"] = get_current_datetime_in_timezone().isoformat()
                    logging.info(f"✅ Mudança no conteúdo do PDF detectada. Novo hash: {hash_atual[:10]}...")
            
                self.estado['medicamentos_em_falta_ultimo'] = medicamentos_em_falta
                self.atualizar_historico_status(medicamentos_em_falta)
            
                modo_envio = os.environ.get("FLAG_EMAIL_TELEGRAM_BOTH", "EMAIL").strip().upper()
            
                mensagem_simples = f"""
            📋 Notificação Automática:
            
            Data/hora: {get_current_datetime_in_timezone().strftime("%d/%m/%Y %H:%M:%S")}
            
            Medicamentos em falta: {', '.join(medicamentos_em_falta) if medicamentos_em_falta else 'Nenhum'}
            Medicamentos disponíveis: {', '.join(medicamentos_disponiveis) if medicamentos_disponiveis else 'Nenhum'}
            
            Execução #{self.estado.get('execucoes', 0)} | Mudanças detectadas: {self.estado.get('mudancas', 0)}
                """
            
                if modo_envio == "EMAIL":
                    self.enviar_email_melhorado(medicamentos_em_falta, medicamentos_disponiveis)
                elif modo_envio == "TELEGRAM":
                    self.enviar_telegram(mensagem_simples)
                elif modo_envio == "BOTH":
                    self.enviar_email_melhorado(medicamentos_em_falta, medicamentos_disponiveis)
                    self.enviar_telegram(mensagem_simples)
                else:
                    logging.warning(f"Modo de envio desconhecido: '{modo_envio}'. Nenhuma notificação enviada.")
            
                if not mudou_conteudo and not mudou_status_medicamentos and os.environ.get("FORCE_EMAIL", "false").lower() == "true":
                    logging.info("📤 Notificação forçada enviada (nenhuma mudança detectada).")
                elif not mudou_conteudo and mudou_status_medicamentos:
                    logging.info("✅ Mudança no status dos medicamentos detectada. Notificação enviada.")
                elif mudou_conteudo:
                    logging.info("✅ Mudança no conteúdo do PDF detectada. Notificação enviada.")
            else:
                logging.info("ℹ️ Nenhuma mudança detectada. Nenhuma notificação enviada.")

            
            self.estado["erros_consecutivos"] = 0
            self.estado["ultima_execucao_sucesso"] = get_current_datetime_in_timezone().isoformat()
            
            duracao = (get_current_datetime_in_timezone() - inicio).total_seconds()
            logging.info(f"Execução concluída em {duracao:.2f}s")
            
        except Exception as e:
            self.estado["erros_consecutivos"] = self.estado.get("erros_consecutivos", 0) + 1
            logging.error(f"Erro na execução: {e}", exc_info=True)
            
        finally:
            self.salvar_estado(self.estado)
            
            if os.path.exists(self.arquivo_temp):
                try:
                    os.remove(self.arquivo_temp)
                    logging.info(f"Arquivo temporário {self.arquivo_temp} removido.")
                except Exception as e:
                    logging.warning(f"Não foi possível remover o arquivo temporário {self.arquivo_temp}: {e}")

    def deve_executar_agora(self) -> bool:
        """Verifica se deve executar baseado nas configurações"""
        agora = get_current_datetime_in_timezone()
        
        # Verifica dia da semana (0=Segunda, 6=Domingo)
        if agora.weekday() not in SCHEDULE_CONFIG['dias_semana']:
            logging.debug(f"Não executa hoje ({agora.strftime('%A')}) - fora dos dias permitidos.")
            return False
            
        # Verifica fins de semana (redundante se dias_semana for configurado corretamente, mas mantém para clareza)
        if not SCHEDULE_CONFIG['executar_fins_semana'] and agora.weekday() >= 5:
            logging.debug(f"Não executa hoje ({agora.strftime('%A')}) - fins de semana desativados.")
            return False
            
        return True

    def status_sistema(self) -> dict:
        """Retorna status do sistema"""
        return {
            'execucoes': self.estado.get('execucoes', 0),
            'mudancas': self.estado.get('mudancas', 0),
            'erros_consecutivos': self.estado.get('erros_consecutivos', 0),
            'ultima_execucao_sucesso': self.estado.get('ultima_execucao_sucesso', 'N/A'),
            'ultima_mudanca': self.estado.get('ultima_mudanca', 'N/A'),
            'biblioteca_pdf_usada': PDF_LIBRARY,
            'arquivo_id_monitorado': self.file_id,
            'estado_atual_hash': self.estado.get('hash_ultimo', '')[:10] + '...' if self.estado.get('hash_ultimo') else 'N/A',
            'medicamentos_em_falta_ultimo': self.estado.get('medicamentos_em_falta_ultimo', [])
        }

def configurar_schedule():
    """Configura o agendamento baseado nas configurações"""
    monitor = PDFMonitor(ARQUIVO_ID)
    
    # Limpa agendamentos anteriores para evitar duplicação em caso de reconfiguração
    schedule.clear()
    
    if SCHEDULE_CONFIG['modo'] == 'horarios_especificos':
        for horario in SCHEDULE_CONFIG['horarios_especificos']:
            # A função `do` deve receber uma função sem argumentos, então usamos lambda
            schedule.every().day.at(horario).do(lambda: monitor.executar() if monitor.deve_executar_agora() else logging.info(f"Execução pulada às {horario} - fora dos dias permitidos."))
            logging.info(f"Agendamento configurado para: {horario}")
    
    dias_nomes = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    dias_permitidos = [dias_nomes[i] for i in SCHEDULE_CONFIG['dias_semana']]
    logging.info(f"Dias da semana permitidos para execução: {', '.join(dias_permitidos)}")
    logging.info(f"Executar nos fins de semana: {SCHEDULE_CONFIG['executar_fins_semana']}")
    
    return monitor

def executar_schedule():
    """Executa o loop principal do schedule"""
    monitor = configurar_schedule()
    
    logging.info("=== MONITOR DE PDF INICIADO EM MODO AGENDADO ===")
    logging.info("Pressione Ctrl+C para parar (se executando localmente)")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Monitor interrompido pelo usuário.")
    except Exception as e:
        logging.error(f"Erro fatal no loop do schedule: {e}", exc_info=True)

def main():
    """Função principal"""
    force_email_arg = False
    # Verifica se --force-email está presente e seu valor
    if '--force-email' in sys.argv:
        try:
            force_email_index = sys.argv.index('--force-email')
            if force_email_index + 1 < len(sys.argv):
                force_email_str = sys.argv[force_email_index + 1].lower()
                force_email_arg = force_email_str == 'true' or force_email_str == '1'
        except ValueError:
            pass

    if '--single' in sys.argv:
        logging.info("=== MODO DE EXECUÇÃO ÚNICA (TESTE) ===")
        monitor = PDFMonitor(ARQUIVO_ID)
        monitor.executar()
    elif '--status' in sys.argv:
        logging.info("=== MODO DE VERIFICAÇÃO DE STATUS ===")
        monitor = PDFMonitor(ARQUIVO_ID)
        status = monitor.status_sistema()
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        executar_schedule()

if __name__ == "__main__":
    main()
