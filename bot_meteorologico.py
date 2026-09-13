import discord
from discord import app_commands
import requests
import asyncio
import json
import os
from datetime import datetime

# ============================================================
# CONFIGURAÇÃO
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN", "COLOQUE_SEU_TOKEN_AQUI")
INTERVALO = 600  # 10 minutos
ARQUIVO_CONFIG = "config.json"

# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()

class WeatherBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.config = {}
        self.ultimo_nivel = {}

bot = WeatherBot()

# ============================================================
# CONFIGURAÇÃO PERSISTENTE
# ============================================================

def carregar_config():
    if not os.path.exists(ARQUIVO_CONFIG):
        return {}

    try:
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def salvar_config():
    with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
        json.dump(bot.config, f, indent=4, ensure_ascii=False)

# ============================================================
# GEOCODIFICAÇÃO
# ============================================================

def procurar_cidade(nome):
    url = "https://geocoding-api.open-meteo.com/v1/search"

    params = {
        "name": nome,
        "count": 5,
        "language": "pt",
        "format": "json"
    }

    resposta = requests.get(url, params=params, timeout=20)
    dados = resposta.json()
    resultados = dados.get("results", [])

    if not resultados:
        return None

    r = resultados[0]

    return {
        "nome": r.get("name"),
        "estado": r.get("admin1", ""),
        "pais": r.get("country", ""),
        "latitude": r["latitude"],
        "longitude": r["longitude"],
        "timezone": r.get("timezone", "auto")
    }

# ============================================================
# CONSULTA METEOROLÓGICA
# ============================================================

def consultar_meteorologia(local):
    latitude = local["latitude"]
    longitude = local["longitude"]

    hourly = [
        "temperature_2m",
        "relative_humidity_2m",
        "dew_point_2m",
        "precipitation",
        "rain",
        "showers",
        "weather_code",
        "pressure_msl",
        "surface_pressure",
        "cloud_cover",
        "cloud_cover_low",
        "cloud_cover_mid",
        "cloud_cover_high",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_gusts_10m",
        "cape",
        "cin",
        "lifted_index",
        "freezing_level_height",
        "boundary_layer_height",
        "wind_speed_950hPa",
        "wind_direction_950hPa",
        "wind_speed_925hPa",
        "wind_direction_925hPa",
        "wind_speed_850hPa",
        "wind_direction_850hPa",
        "wind_speed_700hPa",
        "wind_direction_700hPa",
        "wind_speed_500hPa",
        "wind_direction_500hPa",
        "wind_speed_300hPa",
        "wind_direction_300hPa"
    ]

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(hourly),
        "forecast_days": 2,
        "timezone": local["timezone"],
        "wind_speed_unit": "kmh"
    }

    url = "https://api.open-meteo.com/v1/forecast"

    resposta = requests.get(url, params=params, timeout=30)
    resposta.raise_for_status()

    return resposta.json()

# ============================================================
# UTILIDADES
# ============================================================

def valor(dados, nome, indice=0):
    try:
        v = dados["hourly"][nome][indice]

        if v is None:
            return 0

        return float(v)
    except:
        return 0

def diferenca_direcao(a, b):
    diferenca = abs(a - b)

    if diferenca > 180:
        diferenca = 360 - diferenca

    return diferenca

def calcular_shear_0_6km(dados):
    vento_superficie = valor(dados, "wind_speed_10m")
    vento_500 = valor(dados, "wind_speed_500hPa")

    return abs(vento_500 - vento_superficie)

def calcular_shear_direcional(dados):
    d10 = valor(dados, "wind_direction_10m")
    d850 = valor(dados, "wind_direction_850hPa")
    d500 = valor(dados, "wind_direction_500hPa")

    return (
        diferenca_direcao(d10, d850)
        + diferenca_direcao(d850, d500)
    )

# ============================================================
# ANÁLISE METEOROLÓGICA
# ============================================================

def analisar(dados):
    cape = valor(dados, "cape")
    cin = valor(dados, "cin")
    li = valor(dados, "lifted_index")

    dewpoint = valor(dados, "dew_point_2m")
    temperatura = valor(dados, "temperature_2m")
    umidade = valor(dados, "relative_humidity_2m")

    chuva = valor(dados, "precipitation")
    vento = valor(dados, "wind_speed_10m")
    rajada = valor(dados, "wind_gusts_10m")
    pressao = valor(dados, "pressure_msl")
    cloud = valor(dados, "cloud_cover")

    shear = calcular_shear_0_6km(dados)
    directional = calcular_shear_direcional(dados)

    vento_850 = valor(dados, "wind_speed_850hPa")
    vento_700 = valor(dados, "wind_speed_700hPa")
    vento_500 = valor(dados, "wind_speed_500hPa")

    score = 0
    fatores = []

    # CAPE
    if cape >= 3500:
        score += 30
        fatores.append("CAPE extremamente alto")
    elif cape >= 2500:
        score += 24
        fatores.append("CAPE muito alto")
    elif cape >= 1800:
        score += 17
        fatores.append("CAPE alto")
    elif cape >= 1000:
        score += 9
        fatores.append("CAPE moderado")
    elif cape >= 500:
        score += 4

    # CIN
    if cin > -25:
        score += 8
        fatores.append("CIN fraco")
    elif cin > -75:
        score += 3
    elif cin < -150:
        score -= 5
        fatores.append("CIN forte")

    # Lifted Index
    if li <= -6:
        score += 12
        fatores.append("LI extremamente instável")
    elif li <= -4:
        score += 9
        fatores.append("LI muito instável")
    elif li <= -2:
        score += 5
        fatores.append("LI instável")

    # Ponto de orvalho
    if dewpoint >= 24:
        score += 10
        fatores.append("Ponto de orvalho muito alto")
    elif dewpoint >= 21:
        score += 7
        fatores.append("Ponto de orvalho alto")
    elif dewpoint >= 18:
        score += 4
        fatores.append("Boa umidade para convecção")

    # Shear
    if shear >= 45:
        score += 20
        fatores.append("Cisalhamento 0–6 km muito forte")
    elif shear >= 30:
        score += 14
        fatores.append("Cisalhamento 0–6 km forte")
    elif shear >= 20:
        score += 8
        fatores.append("Cisalhamento moderado")
    elif shear >= 12:
        score += 4

    # Mudança direcional
    if directional >= 120:
        score += 18
        fatores.append("Forte mudança direcional do vento")
    elif directional >= 80:
        score += 12
        fatores.append("Mudança direcional significativa")
    elif directional >= 45:
        score += 6
        fatores.append("Alguma mudança direcional")

    # Vento em altitude
    if vento_850 >= 60:
        score += 8
        fatores.append("Vento forte em 850 hPa")

    if vento_700 >= 70:
        score += 8
        fatores.append("Vento forte em 700 hPa")

    if vento_500 >= 80:
        score += 8
        fatores.append("Vento muito forte em 500 hPa")

    # Rajadas
    if rajada >= 100:
        score += 20
        fatores.append("Rajadas extremas")
    elif rajada >= 80:
        score += 14
        fatores.append("Rajadas muito fortes")
    elif rajada >= 60:
        score += 8
        fatores.append("Rajadas fortes")
    elif rajada >= 45:
        score += 4

    # Chuva
    if chuva >= 30:
        score += 15
        fatores.append("Chuva muito intensa")
    elif chuva >= 15:
        score += 10
        fatores.append("Chuva intensa")
    elif chuva >= 8:
        score += 5
        fatores.append("Chuva forte")

    # Umidade
    if umidade >= 85:
        score += 4

    score = max(0, min(score, 150))

    if score >= 100:
        nivel = "ROXO"
        cor = discord.Color.purple()
    elif score >= 70:
        nivel = "VERMELHO"
        cor = discord.Color.red()
    elif score >= 40:
        nivel = "LARANJA"
        cor = discord.Color.orange()
    elif score >= 20:
        nivel = "AMARELO"
        cor = discord.Color.yellow()
    else:
        nivel = "NORMAL"
        cor = discord.Color.green()

    return {
        "nivel": nivel,
        "cor": cor,
        "score": score,
        "cape": cape,
        "cin": cin,
        "li": li,
        "temperatura": temperatura,
        "dewpoint": dewpoint,
        "umidade": umidade,
        "chuva": chuva,
        "vento": vento,
        "rajada": rajada,
        "pressao": pressao,
        "cloud": cloud,
        "shear": shear,
        "directional": directional,
        "vento_850": vento_850,
        "vento_700": vento_700,
        "vento_500": vento_500,
        "fatores": fatores
    }

# ============================================================
# EMBED
# ============================================================

def criar_embed(local, analise):
    nivel = analise["nivel"]

    emoji = {
        "NORMAL": "🟢",
        "AMARELO": "🟡",
        "LARANJA": "🟠",
        "VERMELHO": "🔴",
        "ROXO": "🟣"
    }[nivel]

    embed = discord.Embed(
        title=f"{emoji} ALERTA METEOROLÓGICO — {nivel}",
        description=(
            f"**{local['nome']} — {local['estado']}**\n"
            f"Índice experimental de severidade: "
            f"**{analise['score']}/150**"
        ),
        color=analise["cor"],
        timestamp=datetime.utcnow()
    )

    embed.add_field(
        name="⚡ Instabilidade",
        value=(
            f"CAPE: **{analise['cape']:.0f} J/kg**\n"
            f"CIN: **{analise['cin']:.0f} J/kg**\n"
            f"LI: **{analise['li']:.1f}**"
        ),
        inline=True
    )

    embed.add_field(
        name="💧 Termodinâmica",
        value=(
            f"Temp.: **{analise['temperatura']:.1f} °C**\n"
            f"Orvalho: **{analise['dewpoint']:.1f} °C**\n"
            f"Umidade: **{analise['umidade']:.0f}%**"
        ),
        inline=True
    )

    embed.add_field(
        name="💨 Vento",
        value=(
            f"Superfície: **{analise['vento']:.0f} km/h**\n"
            f"Rajada: **{analise['rajada']:.0f} km/h**\n"
            f"Shear: **{analise['shear']:.0f} km/h**"
        ),
        inline=True
    )

    embed.add_field(
        name="🌬️ Altitude",
        value=(
            f"850 hPa: **{analise['vento_850']:.0f} km/h**\n"
            f"700 hPa: **{analise['vento_700']:.0f} km/h**\n"
            f"500 hPa: **{analise['vento_500']:.0f} km/h**"
        ),
        inline=True
    )

    embed.add_field(
        name="🌧️ Chuva",
        value=f"**{analise['chuva']:.1f} mm/h**",
        inline=True
    )

    embed.add_field(
        name="🔄 Mudança de direção",
        value=f"**{analise['directional']:.0f}°**",
        inline=True
    )

    fatores = analise["fatores"]

    texto = (
        "\n".join(f"• {f}" for f in fatores[:10])
        if fatores
        else "Nenhum fator significativo."
    )

    embed.add_field(
        name="🔎 Fatores detectados",
        value=texto,
        inline=False
    )

    embed.set_footer(
        text="Análise automática experimental • Atualização a cada 10 minutos"
    )

    return embed

# ============================================================
# MONITORAMENTO
# ============================================================

async def monitorar(guild_id):
    await bot.wait_until_ready()

    while True:
        try:
            config = bot.config.get(str(guild_id))

            if not config:
                return

            canal_id = config.get("canal")

            if not canal_id:
                return

            canal = bot.get_channel(int(canal_id))

            if canal is None:
                await asyncio.sleep(INTERVALO)
                continue

            local = config["local"]

            dados = consultar_meteorologia(local)
            analise = analisar(dados)
            nivel = analise["nivel"]

            ultimo = bot.ultimo_nivel.get(guild_id)

            if nivel != ultimo:
                embed = criar_embed(local, analise)

                await canal.send(
                    content="🚨 **MUDANÇA DE NÍVEL METEOROLÓGICO**",
                    embed=embed
                )

                bot.ultimo_nivel[guild_id] = nivel

            print(
                f"[{local['nome']}] "
                f"{nivel} "
                f"Score={analise['score']}"
            )

        except Exception as erro:
            print(
                f"Erro no monitoramento {guild_id}:",
                erro
            )

        await asyncio.sleep(INTERVALO)

# ============================================================
# /CIDADE
# ============================================================

@bot.tree.command(
    name="cidade",
    description="Escolhe a cidade que será monitorada"
)
@app_commands.describe(
    nome="Nome da cidade"
)
async def cidade(
    interaction: discord.Interaction,
    nome: str
):
    await interaction.response.defer()

    local = procurar_cidade(nome)

    if not local:
        await interaction.followup.send(
            "❌ Não encontrei essa cidade."
        )
        return

    guild_id = str(interaction.guild_id)

    if guild_id not in bot.config:
        bot.config[guild_id] = {}

    bot.config[guild_id]["local"] = local

    salvar_config()

    await interaction.followup.send(
        f"📍 Cidade definida para **{local['nome']} - "
        f"{local['estado']}**.\n\n"
        f"Latitude: `{local['latitude']}`\n"
        f"Longitude: `{local['longitude']}`"
    )

# ============================================================
# /CANAL
# ============================================================

@bot.tree.command(
    name="canal",
    description="Define o canal onde os alertas serão enviados"
)
async def canal(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)

    if guild_id not in bot.config:
        bot.config[guild_id] = {}

    bot.config[guild_id]["canal"] = interaction.channel_id

    salvar_config()

    await interaction.response.send_message(
        "✅ Este canal agora receberá os alertas meteorológicos."
    )

# ============================================================
# /TEMPO
# ============================================================

@bot.tree.command(
    name="tempo",
    description="Mostra a análise meteorológica atual"
)
async def tempo(interaction: discord.Interaction):
    await interaction.response.defer()

    guild_id = str(interaction.guild_id)
    config = bot.config.get(guild_id)

    if not config or "local" not in config:
        await interaction.followup.send(
            "❌ Primeiro use `/cidade NomeDaCidade`."
        )
        return

    try:
        local = config["local"]
        dados = consultar_meteorologia(local)
        analise = analisar(dados)

        embed = criar_embed(local, analise)

        await interaction.followup.send(embed=embed)

    except Exception as erro:
        await interaction.followup.send(
            f"❌ Erro ao consultar os dados: `{erro}`"
        )

# ============================================================
# /STATUS
# ============================================================

@bot.tree.command(
    name="status",
    description="Mostra o status do monitoramento"
)
async def status(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    config = bot.config.get(guild_id)

    if not config:
        await interaction.response.send_message(
            "❌ Monitoramento ainda não configurado."
        )
        return

    local = config.get("local")

    await interaction.response.send_message(
        f"🟢 **Monitoramento ativo**\n\n"
        f"📍 Cidade: **{local['nome']} - {local['estado']}**\n"
        f"⏱️ Intervalo: **10 minutos**\n"
        f"📡 Fonte: **Open-Meteo**"
    )

# ============================================================
# INICIALIZAÇÃO
# ============================================================

@bot.event
async def on_ready():
    bot.config = carregar_config()

    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} comandos sincronizados.")
    except Exception as erro:
        print("Erro sincronizando comandos:", erro)

    print(f"🤖 Bot conectado como {bot.user}")

    for guild_id in bot.config:
        asyncio.create_task(monitorar(guild_id))

bot.run(TOKEN)
