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
from datetime import datetime, timedelta
from pathlib import Path
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sys
import schedule
import threading
from typing import List, Dict, Optional, Tuple
import re

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
ARQUIVO_ID = '1ldltNZuBwIBfEE83mTOvzGrw_7HQEc-l'
PASTA_DADOS = 'dados_monitoramento'
ARQUIVO_ESTADO = 'estado_monitor.json'
ARQUIVO_LOG = 'log_monitor.log'
ARQUIVO_TEMP = 'temp.pdf'
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
        '05:00',  # Antes do inicio do atendimento
        '06:00',  # Inicio do atendimento
        '07:00',  # Durante atendimento
        '08:00',  # Durante atendimento
        '09:00',  # Durante atendimento
        '10:00',  # Durante atendimento
        '11:00',  # Durante atendimento
        '12:00',  # Durante atendimento
        '13:00',  # Durante atendimento
        '14:00',  # Fim do atendimento
        '23:00',  # Final do dia
    ],
    'executar_fins_semana': False,
    'dias_semana': [0, 1, 2, 3, 4],  # Segunda a sexta
}

class PDFMonitor:
    def __init__(self, file_id: str, force_email: bool = False):
        self.file_id = file_id
        self.pasta_dados = PASTA_DADOS
        self.arquivo_estado = os.path.join(self.pasta_dados, ARQUIVO_ESTADO)
        self.arquivo_log = os.path.join(self.pasta_dados, ARQUIVO_LOG)
        self.arquivo_temp = os.path.join(self.pasta_dados, ARQUIVO_TEMP)
        self.force_email = force_email
        self.setup()

    def setup(self):
        """Inicializa o monitor"""
        Path(self.pasta_dados).mkdir(exist_ok=True)
        self.setup_logging()
        self.estado = self.carregar_estado()
        self.log_system_info()

    def setup_logging(self):
        """Configura o sistema de logging"""
        # Remove handlers existentes
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        
        # Configuração do logging
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
        logging.info(f"Python: {sys.version}")
        logging.info(f"Arquivo ID: {self.file_id}")
        logging.info(f"Medicamentos monitorados: {', '.join(PALAVRAS_CHAVE)}")
        logging.info(f"Forçar envio de e-mail: {self.force_email}")

    def carregar_estado(self) -> Dict:
        """Carrega o estado anterior ou cria um novo"""
        if os.path.exists(self.arquivo_estado):
            try:
                with open(self.arquivo_estado, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        logging.warning("Arquivo de estado vazio. Criando estado inicial.")
                        return self.estado_inicial()
                    return json.loads(content)
            except (json.JSONDecodeError, Exception) as e:
                logging.error(f"Erro ao carregar estado: {e}. Criando estado inicial.")
                return self.estado_inicial()
        else:
            logging.info("Primeiro uso. Criando estado inicial.")
            return self.estado_inicial()
    
    def estado_inicial(self) -> Dict:
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
        self.salvar_estado(estado)
        return estado

    def salvar_estado(self, estado: Dict):
        """Salva o estado atual"""
        estado['data_atualizacao'] = datetime.now().isoformat()
        try:
            with open(self.arquivo_estado, 'w', encoding='utf-8') as f:
                json.dump(estado, f, ensure_ascii=False, indent=2)
            logging.info("Estado salvo com sucesso")
        except Exception as e:
            logging.error(f"Erro ao salvar estado: {e}")

    def baixar_pdf_com_retry(self) -> bool:
        """Baixa o PDF com retry automático"""
        for tentativa in range(RETRY_CONFIG['max_retries']):
            try:
                logging.info(f"Tentativa {tentativa + 1} de download...")
                
                # Diferentes estratégias de download
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
                        
                        # Verifica se é realmente um PDF
                        content_type = response.headers.get('content-type', '')
                        if 'pdf' not in content_type.lower():
                            logging.warning(f"Conteúdo pode não ser PDF: {content_type}")
                        
                        # Salva o arquivo
                        with open(self.arquivo_temp, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        
                        # Verifica se o arquivo foi salvo corretamente
                        if os.path.getsize(self.arquivo_temp) > 0:
                            logging.info(f"Download concluído ({os.path.getsize(self.arquivo_temp)} bytes)")
                            return True
                        else:
                            logging.error("Arquivo PDF está vazio")
                            
                    except requests.exceptions.RequestException as e:
                        logging.warning(f"Erro na URL {url}: {e}")
                        continue
                
                # Se chegou aqui, todas as URLs falharam
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
                logging.warning(f"Erro com PyPDF2: {e}")
        
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
            logging.warning(f"pdfplumber não disponível ou erro: {e}")
        
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
            logging.warning(f"PyMuPDF não disponível ou erro: {e}")
        
        logging.error("Falha na extração de texto com todas as bibliotecas")
        return ""

    def limpar_texto(self, texto: str) -> str:
        """Limpa e normaliza o texto extraído"""
        if not texto:
            return ""
        
        # Remove caracteres de controle e normaliza espaços
        texto = re.sub(r'\s+', ' ', texto.strip())
        
        # Remove caracteres especiais problemáticos
        texto = re.sub(r'[^\w\s\-\.,;:!?()]', '', texto)
        
        return texto

    def calcular_hash(self, texto: str) -> str:
        """Calcula hash MD5 do texto"""
        return hashlib.md5(texto.encode('utf-8')).hexdigest() if texto else ""

    def verificar_palavras_chave(self, texto: str) -> Tuple[List[str], List[str]]:
        """Verifica medicamentos em falta e disponíveis"""
        texto_lower = texto.lower()
        encontradas = []
        nao_encontradas = []
        
        for medicamento in PALAVRAS_CHAVE:
            if medicamento.lower() in texto_lower:
                encontradas.append(medicamento)
            else:
                nao_encontradas.append(medicamento)
        
        logging.info(f"Medicamentos em falta: {encontradas}")
        logging.info(f"Medicamentos disponíveis: {nao_encontradas}")
        
        return encontradas, nao_encontradas

    def criar_email_html(self, encontradas: List[str], nao_encontradas: List[str]) -> str:
        """Cria conteúdo HTML para o email"""
        agora = datetime.now()
        data_hora = agora.strftime("%d/%m/%Y às %H:%M:%S")
        
        # Cor baseada no status
        cor_status = "#dc3545" if encontradas else "#28a745"  # vermelho se falta, verde se ok
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .header {{ background-color: {cor_status}; color: white; padding: 15px; border-radius: 5px; }}
                .content {{ padding: 20px; border: 1px solid #ddd; border-radius: 5px; margin-top: 10px; }}
                .medicamento {{ padding: 5px; margin: 5px 0; border-radius: 3px; }}
                .falta {{ background-color: #f8d7da; color: #721c24; }}
                .disponivel {{ background-color: #d4edda; color: #155724; }}
                .info {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 10px 0; }}
                .stats {{ font-size: 0.9em; color: #666; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2>🚨 Atualização de Medicamentos - {data_hora}</h2>
            </div>
            
            <div class="content">
                <h3>Status dos Medicamentos:</h3>
                
                {f'''
                <h4 style="color: #dc3545;">⚠️ Medicamentos em Falta:</h4>
                {''.join(f'<div class="medicamento falta">• {med}</div>' for med in encontradas)}
                ''' if encontradas else ''}
                
                {f'''
                <h4 style="color: #28a745;">✅ Medicamentos Disponíveis:</h4>
                {''.join(f'<div class="medicamento disponivel">• {med}</div>' for med in nao_encontradas)}
                ''' if nao_encontradas else ''}
                
                <div class="info">
                    <h4>Informações da Execução:</h4>
                    <div class="stats">
                        <p><strong>Data/Hora:</strong> {data_hora}</p>
                        <p><strong>Execução #:</strong> {self.estado['execucoes']}</p>
                        <p><strong>Total de mudanças:</strong> {self.estado['mudancas']}</p>
                        <p><strong>Hash do conteúdo:</strong> {self.estado['hash_ultimo'][:8]}...</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html

    def enviar_email_melhorado(self, encontradas: List[str], nao_encontradas: List[str]):
        """Envia email com formatação HTML melhorada"""
        try:
            remetente = os.environ["EMAIL_USUARIO"]
            senha = os.environ["EMAIL_SENHA"]
            destinatario = "edunaka@live.com"
            
            agora = datetime.now()
            data_hora_subject = agora.strftime("%d/%m/%Y %H:%M")
            
            # Cria mensagem multipart
            msg = MIMEMultipart('alternative')
            
            # Subject baseado no status
            if encontradas:
                status_emoji = "🚨"
                status_text = f"FALTA: {', '.join(encontradas)}"
            else:
                status_emoji = "✅"
                status_text = "TODOS DISPONÍVEIS"
            
            msg["Subject"] = f"{status_emoji} Medicamentos - {data_hora_subject} ({status_text})"
            msg["From"] = remetente
            msg["To"] = destinatario
            
            # Prioridade alta
            msg["X-Priority"] = "1"
            msg["Importance"] = "High"
            
            # Versão texto simples
            texto_simples = f"""
Execução realizada em: {agora.strftime("%d/%m/%Y às %H:%M:%S")}

O PDF foi atualizado.

Medicamentos em falta: {', '.join(encontradas) if encontradas else 'Nenhum'}
Medicamentos disponíveis: {', '.join(nao_encontradas) if nao_encontradas else 'Nenhum'}

Execução #{self.estado['execucoes']} | Total de mudanças: {self.estado['mudancas']}
            """
            
            # Versão HTML
            html_content = self.criar_email_html(encontradas, nao_encontradas)
            
            # Anexa ambas as versões
            msg.attach(MIMEText(texto_simples, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # Envia email
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(remetente, senha)
            server.sendmail(remetente, [destinatario], msg.as_string())
            server.quit()
            
            logging.info(f"Email enviado com sucesso: {status_text}")
            
        except Exception as e:
            logging.error(f"Erro ao enviar email: {e}")

    def atualizar_historico_status(self, encontradas: List[str]):
        """Atualiza histórico de status dos medicamentos"""
        status_atual = {
            'timestamp': datetime.now().isoformat(),
            'medicamentos_falta': encontradas,
            'total_falta': len(encontradas)
        }
        
        # Mantém apenas os últimos 50 registros
        if 'historico_status' not in self.estado:
            self.estado['historico_status'] = []
        
        self.estado['historico_status'].append(status_atual)
        self.estado['historico_status'] = self.estado['historico_status'][-50:]

    def executar(self):
        """Executa o monitoramento principal"""
        self.estado["execucoes"] += 1
        inicio = datetime.now()
        
        logging.info(f"=== EXECUÇÃO #{self.estado['execucoes']} INICIADA ===")
        
        try:
            # Download do PDF
            if not self.baixar_pdf_com_retry():
                self.estado["erros_consecutivos"] += 1
                logging.error(f"Erro consecutivo #{self.estado['erros_consecutivos']}")
                return
            
            # Extração de texto
            texto = self.extrair_texto_multiplas_bibliotecas()
            if not texto:
                self.estado["erros_consecutivos"] += 1
                logging.error("Falha na extração de texto")
                return
            
            # Verifica mudanças
            hash_atual = self.calcular_hash(texto)
            mudou = hash_atual != self.estado["hash_ultimo"]
            
            if mudou or self.force_email: # Adiciona a condição force_email aqui
                if mudou:
                    self.estado["mudancas"] += 1
                    self.estado["texto_ultimo"] = texto
                    self.estado["hash_ultimo"] = hash_atual
                    self.estado["data_ultimo"] = datetime.now().isoformat()
                    self.estado["ultima_mudanca"] = datetime.now().isoformat()
                
                # Verifica medicamentos
                encontradas, nao_encontradas = self.verificar_palavras_chave(texto)
                
                # Atualiza histórico
                self.atualizar_historico_status(encontradas)
                
                # Envia email
                self.enviar_email_melhorado(encontradas, nao_encontradas)
                
                if mudou:
                    logging.info(f"✅ Mudança detectada e processada")
                else:
                    logging.info(f"📧 E-mail forçado enviado (nenhuma mudança detectada)")
            else:
                logging.info("ℹ️  Nenhuma mudança detectada")
            
            # Reset contador de erros em caso de sucesso
            self.estado["erros_consecutivos"] = 0
            self.estado["ultima_execucao_sucesso"] = datetime.now().isoformat()
            
            # Estatísticas
            duracao = (datetime.now() - inicio).total_seconds()
            logging.info(f"Execução concluída em {duracao:.2f}s")
            
        except Exception as e:
            self.estado["erros_consecutivos"] += 1
            logging.error(f"Erro na execução: {e}")
            
        finally:
            self.salvar_estado(self.estado)
            
            # Limpa arquivo temporário
            if os.path.exists(self.arquivo_temp):
                os.remove(self.arquivo_temp)

    def deve_executar_agora(self) -> bool:
        """Verifica se deve executar baseado nas configurações"""
        agora = datetime.now()
        
        # Verifica dia da semana
        if agora.weekday() not in SCHEDULE_CONFIG['dias_semana']:
            return False
            
        # Verifica fins de semana
        if not SCHEDULE_CONFIG['executar_fins_semana'] and agora.weekday() >= 5:
            return False
            
        return True

    def status_sistema(self) -> Dict:
        """Retorna status do sistema"""
        return {
            'execucoes': self.estado['execucoes'],
            'mudancas': self.estado['mudancas'],
            'erros_consecutivos': self.estado['erros_consecutivos'],
            'ultima_execucao_sucesso': self.estado.get('ultima_execucao_sucesso', 'N/A'),
            'ultima_mudanca': self.estado.get('ultima_mudanca', 'N/A'),
            'biblioteca_pdf': PDF_LIBRARY,
            'arquivo_id': self.file_id
        }

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
    
    if SCHEDULE_CONFIG['modo'] == 'horarios_especificos':
        for horario in SCHEDULE_CONFIG['horarios_especificos']:
            schedule.every().day.at(horario).do(monitor.executar)
            logging.info(f"Agendamento configurado para: {horario}")
    
    # Informações sobre configuração
    dias_nomes = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    dias_permitidos = [dias_nomes[i] for i in SCHEDULE_CONFIG['dias_semana']]
    logging.info(f"Dias permitidos: {', '.join(dias_permitidos)}")
    
    return monitor

def executar_schedule():
    """Executa o loop principal do schedule"""
    monitor = configurar_schedule()
    
    logging.info("=== MONITOR DE PDF INICIADO ===")
    logging.info("Pressione Ctrl+C para parar")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Monitor interrompido pelo usuário")
    except Exception as e:
        logging.error(f"Erro no schedule: {e}")

def main():
    """Função principal"""
    force_email_arg = False
    if len(sys.argv) > 1:
        if '--single' in sys.argv:
            # Execução única
            logging.info("=== EXECUÇÃO ÚNICA (TESTE) ===")
            if '--force-email' in sys.argv:
                try:
                    force_email_index = sys.argv.index('--force-email')
                    if force_email_index + 1 < len(sys.argv):
                        force_email_arg = sys.argv[force_email_index + 1].lower() == 'true'
                except ValueError:
                    pass # Should not happen if '--force-email' is in sys.argv
            monitor = PDFMonitor(ARQUIVO_ID, force_email=force_email_arg)
            monitor.executar()
        elif sys.argv[1] == '--status':
            # Status do sistema
            monitor = PDFMonitor(ARQUIVO_ID)
            status = monitor.status_sistema()
            print(json.dumps(status, indent=2, ensure_ascii=False))
        else:
            print("Uso: python script.py [--single [--force-email <true|false>]|--status]")
    else:
        # Execução com schedule
        executar_schedule()

if __name__ == "__main__":
    main()

