from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import random
import asyncio

app = FastAPI(title="HortaSmart API - Simulação Pura")

# Configuração de CORS para conectar com seu Dashboard React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 💾 NOSSO BANCO DE DADOS QUEBRA-GALHO (Começa 100% vazio)
BANCO_FICTICIO = []
bomba_ativa = False

def calcular_proximo_valor(valor_atual, limite_min, limite_max, passo_max=0.5):
    """Garante variação suave baseada no random walk (máximo de 0.5)"""
    direcao = random.choice([-1, 0, 1])
    variacao = direcao * random.uniform(0.0, passo_max)
    novo_valor = round(valor_atual + variacao, 2)
    return max(limite_min, min(limite_max, novo_valor))

def gerar_leitura_ficticia(dt: datetime):
    """Gera leituras reais baseadas estritamente no último valor em memória"""
    global bomba_ativa
    hour = dt.hour

    # Se o banco estiver vazio (primeiríssima leitura), define valores base padrões
    if not BANCO_FICTICIO:
        if 8 <= hour <= 18:
            temp_base, hum_ar_base, hum_solo_base, temp_solo_base = 26.0, 45.0, 65.0, 23.0
        else:
            temp_base, hum_ar_base, hum_solo_base, temp_solo_base = 20.0, 72.0, 65.0, 19.0
        ph_base = 6.5
    else:
        # Pega o último registro real para servir de âncora para o próximo minuto
        ultimo = BANCO_FICTICIO[-1]
        temp_base = ultimo["temperatura"]
        hum_ar_base = ultimo["umidade"]
        hum_solo_base = ultimo["umidade_solo"]
        temp_solo_base = ultimo["temperatura_solo"]
        ph_base = ultimo["PH_solo"]

    # Aplicação das regras de variação controlada (máximo 0.5)
    temp = calcular_proximo_valor(temp_base, 15.0, 35.0, passo_max=0.5)
    temp_solo = calcular_proximo_valor(temp_solo_base, 14.0, 30.0, passo_max=0.2)
    hum_ar = calcular_proximo_valor(hum_ar_base, 30.0, 95.0, passo_max=0.5)
    ph_solo = calcular_proximo_valor(ph_base, 5.5, 7.5, passo_max=0.02)

    # Lógica de Irrigação (Aba de status da bomba)
    if bomba_ativa:
        # Se ligada, a umidade do solo sobe de minuto em minuto
        hum_solo = round(hum_solo_base + random.uniform(0.5, 1.5), 1)
        if hum_solo >= 78.0:
            bomba_ativa = False
    else:
        # Se desligada, o solo seca seguindo a regra geral
        hum_solo = calcular_proximo_valor(hum_solo_base, 40.0, 90.0, passo_max=0.5)
        if hum_solo <= 55.0:
            bomba_ativa = True

    # Luminosidade (Sem restrição de 0.5, baseada na hora do dia)
    if 6 <= hour <= 18:
        fator_solar = random.uniform(0.85, 1.0)
        luminosidade = round(random.uniform(9000.0, 12000.0) * fator_solar, 1) if 11 <= hour <= 14 else round(random.uniform(2000.0, 5000.0) * fator_solar, 1)
    else:
        luminosidade = round(random.uniform(0.0, 15.0), 1)

    return {
        "id": len(BANCO_FICTICIO) + 1,
        "timestamp": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "umidade": hum_ar,
        "umidade_solo": hum_solo,
        "temperatura": temp,
        "temperatura_solo": temp_solo,
        "luminosidade": luminosidade,
        "PH_solo": ph_solo,
        "status_bomba": bomba_ativa,
        "status": "ok"
    }


# 🔄 EMULADOR ATIVO - Gera 1 dado novo a cada 1 minuto (60s)
async def emulador_horta_loop():
    # Gera a primeira leitura imediatamente assim que o servidor liga
    if not BANCO_FICTICIO:
        BANCO_FICTICIO.append(gerar_leitura_ficticia(datetime.now()))
        
    while True:
        await asyncio.sleep(60)  # Aguarda 1 minuto para a próxima leitura
        nova_leitura = gerar_leitura_ficticia(datetime.now())
        BANCO_FICTICIO.append(nova_leitura)
        
        # Mantém até 1000 registros na memória para análise do histórico
        if len(BANCO_FICTICIO) > 1000:
            BANCO_FICTICIO.pop(0)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(emulador_horta_loop())


# 🖥️ ENDPOINTS DA API

@app.get("/api/v1/telemetria/atual")
def obter_leitura_atual():
    """Retorna o dado mais recente gerado neste minuto"""
    if BANCO_FICTICIO:
        return BANCO_FICTICIO[-1]
    return {"erro": "Aguardando primeira leitura do sensor..."}

@app.get("/api/v1/telemetria/historico")
def obter_historico():
    """Retorna tudo o que foi acumulado desde que a API foi ligada"""
    return BANCO_FICTICIO