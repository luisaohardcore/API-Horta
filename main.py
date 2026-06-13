from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from dotenv import load_dotenv
import random
import asyncio
import aiomysql
import ssl
import os

load_dotenv()

app = FastAPI(title="HortaSmart API - Simulação Pura")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------
# CONFIG DB
# -------------------------------------------------------
DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "port":     int(os.getenv("DB_PORT", 4000)),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "db":       os.getenv("DB_NAME"),
}
SSL_CA = os.getenv("DB_SSL_CA")

CANTEIRO_ID   = 1
CANTEIRO_NOME = "Principal"

db_pool = None

# -------------------------------------------------------
# LÓGICA DE SIMULAÇÃO (sem alteração)
# -------------------------------------------------------
BANCO_FICTICIO = []
bomba_ativa    = False

def calcular_proximo_valor(valor_atual, limite_min, limite_max, passo_max=0.5):
    direcao  = random.choice([-1, 0, 1])
    variacao = direcao * random.uniform(0.0, passo_max)
    return max(limite_min, min(limite_max, round(valor_atual + variacao, 2)))

def gerar_leitura_ficticia(dt: datetime):
    global bomba_ativa
    hour = dt.hour

    if not BANCO_FICTICIO:
        if 8 <= hour <= 18:
            temp_base, hum_ar_base, hum_solo_base, temp_solo_base = 26.0, 45.0, 65.0, 23.0
        else:
            temp_base, hum_ar_base, hum_solo_base, temp_solo_base = 20.0, 72.0, 65.0, 19.0
        ph_base = 6.5
    else:
        ultimo        = BANCO_FICTICIO[-1]
        temp_base     = ultimo["temperatura"]
        hum_ar_base   = ultimo["umidade"]
        hum_solo_base = ultimo["umidade_solo"]
        temp_solo_base= ultimo["temperatura_solo"]
        ph_base       = ultimo["PH_solo"]

    temp      = calcular_proximo_valor(temp_base,      15.0, 35.0, 0.5)
    temp_solo = calcular_proximo_valor(temp_solo_base, 14.0, 30.0, 0.2)
    hum_ar    = calcular_proximo_valor(hum_ar_base,    30.0, 95.0, 0.5)
    ph_solo   = calcular_proximo_valor(ph_base,         5.5,  7.5, 0.02)

    if bomba_ativa:
        hum_solo = round(hum_solo_base + random.uniform(0.5, 1.5), 1)
        if hum_solo >= 78.0:
            bomba_ativa = False
    else:
        hum_solo = calcular_proximo_valor(hum_solo_base, 40.0, 90.0, 0.5)
        if hum_solo <= 55.0:
            bomba_ativa = True

    if 6 <= hour <= 18:
        fator_solar  = random.uniform(0.85, 1.0)
        luminosidade = (
            round(random.uniform(9000.0, 12000.0) * fator_solar, 1)
            if 11 <= hour <= 14
            else round(random.uniform(2000.0,  5000.0) * fator_solar, 1)
        )
    else:
        luminosidade = round(random.uniform(0.0, 15.0), 1)

    return {
        "id":               len(BANCO_FICTICIO) + 1,
        "timestamp":        dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "umidade":          hum_ar,
        "umidade_solo":     hum_solo,
        "temperatura":      temp,
        "temperatura_solo": temp_solo,
        "luminosidade":     luminosidade,
        "PH_solo":          ph_solo,
        "status_bomba":     bomba_ativa,
        "status":           "ok",
    }

# -------------------------------------------------------
# PERSISTÊNCIA
# -------------------------------------------------------
async def criar_pool():
    ssl_ctx = None
    if SSL_CA:
        ssl_ctx = ssl.create_default_context(cafile=SSL_CA)

    return await aiomysql.create_pool(
        **DB_CONFIG,
        ssl=ssl_ctx,
        autocommit=True,
        minsize=1,
        maxsize=5,
    )

async def garantir_canteiro():
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO canteiros (id, nome) VALUES (%s, %s)",
                (CANTEIRO_ID, CANTEIRO_NOME),
            )

async def inserir_leitura(leitura: dict):
    dt = datetime.strptime(leitura["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO leituras
                    (timestamp, canteiro_id, umidade, umidade_solo,
                     temperatura, temperatura_solo, ph_solo, luminosidade, status_bomba)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    dt,
                    CANTEIRO_ID,
                    leitura["umidade"],
                    leitura["umidade_solo"],
                    leitura["temperatura"],
                    leitura["temperatura_solo"],
                    leitura["PH_solo"],
                    leitura["luminosidade"],
                    int(leitura["status_bomba"]),
                ),
            )

# -------------------------------------------------------
# LOOP DO EMULADOR
# -------------------------------------------------------
async def emulador_horta_loop():
    if not BANCO_FICTICIO:
        leitura = gerar_leitura_ficticia(datetime.now())
        BANCO_FICTICIO.append(leitura)
        await inserir_leitura(leitura)

    while True:
        await asyncio.sleep(60)
        leitura = gerar_leitura_ficticia(datetime.now())
        BANCO_FICTICIO.append(leitura)
        await inserir_leitura(leitura)

        if len(BANCO_FICTICIO) > 1000:
            BANCO_FICTICIO.pop(0)

# -------------------------------------------------------
# STARTUP / SHUTDOWN
# -------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    global db_pool
    db_pool = await criar_pool()
    await garantir_canteiro()
    asyncio.create_task(emulador_horta_loop())

@app.on_event("shutdown")
async def shutdown_event():
    db_pool.close()
    await db_pool.wait_closed()

# -------------------------------------------------------
# ENDPOINTS (sem alteração)
# -------------------------------------------------------
@app.get("/api/v1/telemetria/atual")
def obter_leitura_atual():
    if BANCO_FICTICIO:
        return BANCO_FICTICIO[-1]
    return {"erro": "Aguardando primeira leitura do sensor..."}

@app.get("/api/v1/telemetria/historico")
def obter_historico():
    return BANCO_FICTICIO