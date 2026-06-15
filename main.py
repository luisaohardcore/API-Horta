from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import random
import asyncio
import aiomysql
import ssl
import math
import os

load_dotenv()

app = FastAPI(title="HortaSmart API - Simulação Multi-Canteiro")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FUSO_BR = timezone(timedelta(hours=-3))

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

db_pool = None

# -------------------------------------------------------
# CANTEIROS
# -------------------------------------------------------
CANTEIROS = [
    {"id": 1, "nome": "Canteiro A"},
    {"id": 2, "nome": "Canteiro B"},
    {"id": 3, "nome": "Canteiro C"},
]

estado = {
    c["id"]: {"historico": [], "bomba_ativa": False}
    for c in CANTEIROS
}

# -------------------------------------------------------
# LÓGICA DE SIMULAÇÃO
# -------------------------------------------------------
def base_temperatura(hora_decimal: float) -> float:
    """Ciclo diário: pico às 14h (32°C), vale às 5h (18°C)."""
    angulo = (hora_decimal - 9.5) * math.pi / 9
    return 25.0 + 7.0 * math.sin(angulo)

def base_temperatura_solo(hora_decimal: float) -> float:
    """Solo: lag de 2h em relação ao ar, amplitude menor."""
    angulo = (hora_decimal - 11.5) * math.pi / 9
    return 22.0 + 4.5 * math.sin(angulo)

def base_umidade_ar(temp_ar: float) -> float:
    """Umidade do ar inversamente proporcional à temperatura."""
    return max(35.0, min(90.0, 85.0 - (temp_ar - 18.0) * 3.0))

def taxa_evaporacao(temp_ar: float, hum_ar: float) -> float:
    """Perda de umidade do solo por minuto. Maior quando quente e ar seco."""
    fator_temp = (temp_ar - 18.0) / 17.0
    fator_ar   = (100.0 - hum_ar) / 65.0
    return max(0.05, min(0.5, fator_temp * 0.3 + fator_ar * 0.2))

def luminosidade_por_hora(hora: int, temp_ar: float) -> float:
    """Lux baseado na hora. Correlação leve com temperatura."""
    if not (6 <= hora <= 18):
        return round(random.uniform(0.0, 15.0), 1)

    if 11 <= hora <= 14:
        lux = random.uniform(8000.0, 12000.0)
    elif 8 <= hora <= 17:
        lux = random.uniform(3000.0, 8000.0)
    else:
        lux = random.uniform(500.0, 3000.0)

    fator_temp = 0.85 + 0.15 * (temp_ar - 18.0) / 14.0
    return round(max(0.0, min(12000.0, lux * fator_temp)), 1)

def passo_suave(atual, alvo, passo_max=0.5, ruido=0.05, mn=None, mx=None):
    """Move 'atual' em direção a 'alvo' com passo máximo + pequeno ruído."""
    delta    = alvo - atual
    movimento = max(-passo_max, min(passo_max, delta))
    valor    = atual + movimento + random.uniform(-ruido, ruido)
    if mn is not None: valor = max(mn, valor)
    if mx is not None: valor = min(mx, valor)
    return round(valor, 2)

def gerar_leitura(canteiro_id: int, dt: datetime) -> dict:
    s    = estado[canteiro_id]
    hist = s["historico"]
    hora = dt.hour + dt.minute / 60.0

    ultimo = hist[-1] if hist else None

    # Temperatura do ar: move suavemente em direção à base sinusoidal
    temp = passo_suave(
        atual=ultimo["temperatura"] if ultimo else base_temperatura(hora),
        alvo=base_temperatura(hora),
        passo_max=0.5, ruido=0.05, mn=15.0, mx=35.0
    )

    # Temperatura do solo: lag de 2h, passo menor
    temp_solo = passo_suave(
        atual=ultimo["temperatura_solo"] if ultimo else base_temperatura_solo(hora),
        alvo=base_temperatura_solo(hora),
        passo_max=0.3, ruido=0.03, mn=14.0, mx=30.0
    )

    # Umidade do ar: move em direção à base (inversa da temp)
    hum_ar = passo_suave(
        atual=ultimo["umidade"] if ultimo else base_umidade_ar(temp),
        alvo=base_umidade_ar(temp),
        passo_max=0.5, ruido=0.05, mn=30.0, mx=95.0
    )

    # pH do solo: quase estático
    ph_solo = passo_suave(
        atual=ultimo["PH_solo"] if ultimo else 6.5,
        alvo=6.5,
        passo_max=0.01, ruido=0.005, mn=5.5, mx=7.5
    )

    # Umidade do solo: evaporação ou irrigação
    hum_solo_ant = ultimo["umidade_solo"] if ultimo else 65.0

    if s["bomba_ativa"]:
        # Umidade sobe mais rápido com irrigação
        hum_solo = round(min(90.0, hum_solo_ant + random.uniform(0.8, 1.5)), 1)
        if hum_solo >= 78.0:
            s["bomba_ativa"] = False
        # Temperatura do solo cai por resfriamento evaporativo
        temp_solo = passo_suave(
            atual=temp_solo,
            alvo=temp_solo - random.uniform(0.3, 0.8),
            passo_max=0.5, ruido=0.02, mn=14.0, mx=30.0
        )
    else:
        evap     = taxa_evaporacao(temp, hum_ar)
        hum_solo = round(max(40.0, hum_solo_ant - evap + random.uniform(-0.05, 0.05)), 1)
        if hum_solo <= 55.0:
            s["bomba_ativa"] = True

    luminosidade = luminosidade_por_hora(dt.hour, temp)

    return {
        "id":               len(hist) + 1,
        "canteiro_id":      canteiro_id,
        "timestamp":        dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "umidade":          hum_ar,
        "umidade_solo":     hum_solo,
        "temperatura":      temp,
        "temperatura_solo": temp_solo,
        "luminosidade":     luminosidade,
        "PH_solo":          ph_solo,
        "status_bomba":     s["bomba_ativa"],
        "status":           "ok",
    }

# -------------------------------------------------------
# PERSISTÊNCIA
# -------------------------------------------------------
async def criar_pool():
    if SSL_CA:
        ssl_ctx = ssl.create_default_context(cafile=SSL_CA)
    else:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode    = ssl.CERT_NONE

    return await aiomysql.create_pool(
        **DB_CONFIG,
        ssl=ssl_ctx,
        autocommit=True,
        minsize=1,
        maxsize=5,
    )

async def garantir_canteiros():
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            for c in CANTEIROS:
                await cur.execute(
                    "INSERT IGNORE INTO canteiros (id, nome) VALUES (%s, %s)",
                    (c["id"], c["nome"]),
                )

async def inserir_leitura(leitura: dict):
    if not db_pool:
        return
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
                    leitura["canteiro_id"],
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
# LOOP POR CANTEIRO
# -------------------------------------------------------
async def emulador_canteiro(canteiro_id: int):
    leitura = gerar_leitura(canteiro_id, datetime.now(FUSO_BR).replace(tzinfo=None))
    estado[canteiro_id]["historico"].append(leitura)
    await inserir_leitura(leitura)

    while True:
        await asyncio.sleep(60)
        leitura = gerar_leitura(canteiro_id, datetime.now(FUSO_BR).replace(tzinfo=None))
        hist    = estado[canteiro_id]["historico"]
        hist.append(leitura)
        await inserir_leitura(leitura)
        if len(hist) > 1000:
            hist.pop(0)

# -------------------------------------------------------
# STARTUP / SHUTDOWN
# -------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    global db_pool
    if DB_CONFIG["host"] and DB_CONFIG["password"]:
        try:
            db_pool = await criar_pool()
            await garantir_canteiros()
            print("INFO: Banco de dados conectado.")
        except Exception as e:
            print(f"WARNING: Banco indisponível, rodando só em memória. ({e})")
    else:
        print("INFO: Variáveis de banco não configuradas, rodando só em memória.")

    for c in CANTEIROS:
        asyncio.create_task(emulador_canteiro(c["id"]))

@app.on_event("shutdown")
async def shutdown_event():
    if db_pool:
        db_pool.close()
        await db_pool.wait_closed()

# -------------------------------------------------------
# ENDPOINTS
# -------------------------------------------------------
class CanteiroCriar(BaseModel):
    nome: str

@app.post("/api/v1/canteiros", status_code=201)
async def criar_canteiro(body: CanteiroCriar):
    novo_id  = max(c["id"] for c in CANTEIROS) + 1
    canteiro = {"id": novo_id, "nome": body.nome}

    if db_pool:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO canteiros (id, nome) VALUES (%s, %s)",
                    (novo_id, body.nome),
                )

    CANTEIROS.append(canteiro)
    estado[novo_id] = {"historico": [], "bomba_ativa": False}
    asyncio.create_task(emulador_canteiro(novo_id))
    return canteiro


class BombaEstado(BaseModel):
    ativa: bool

@app.patch("/api/v1/canteiros/{canteiro_id}/bomba")
def controlar_bomba(canteiro_id: int, body: BombaEstado):
    if canteiro_id not in estado:
        raise HTTPException(status_code=404, detail="Canteiro não encontrado")
    estado[canteiro_id]["bomba_ativa"] = body.ativa
    return {"canteiro_id": canteiro_id, "bomba_ativa": body.ativa}

@app.get("/api/v1/canteiros")
def listar_canteiros():
    return CANTEIROS

@app.get("/api/v1/telemetria/atual")
def leitura_atual_todos():
    return {
        c["id"]: estado[c["id"]]["historico"][-1]
        if estado[c["id"]]["historico"] else None
        for c in CANTEIROS
    }

@app.get("/api/v1/telemetria/atual/{canteiro_id}")
def leitura_atual(canteiro_id: int):
    if canteiro_id not in estado:
        raise HTTPException(status_code=404, detail="Canteiro não encontrado")
    hist = estado[canteiro_id]["historico"]
    if not hist:
        raise HTTPException(status_code=404, detail="Aguardando primeira leitura")
    return hist[-1]

@app.get("/api/v1/telemetria/historico/{canteiro_id}")
def historico(canteiro_id: int):
    if canteiro_id not in estado:
        raise HTTPException(status_code=404, detail="Canteiro não encontrado")
    return estado[canteiro_id]["historico"]