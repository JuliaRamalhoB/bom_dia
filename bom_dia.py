import ctypes
import os
import time
import winsound
from datetime import date, datetime
from zoneinfo import ZoneInfo

import customtkinter as ctk
import icalendar
import recurring_ical_events
import requests
from gtts import gTTS

from segredos import URL_AGENDA

# ---------- CONFIGURAÇÕES (mexe só aqui) ----------
LAT, LON = 40.64, -8.65         # Aveiro
LIMITE_CHUVA = 50               # % a partir da qual conta como "vai chover"
HORA_INICIO, HORA_FIM = 8, 22   # só interessam estas horas do dia
TENTATIVAS = 3                  # quantas vezes "liga"
SEGUNDOS_A_TOCAR = 30           # quanto tempo cada chamada toca
PAUSA_ENTRE_CHAMADAS = 60       # segundos até ligar de novo
FUSO = ZoneInfo("Europe/Lisbon")
FICHEIRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bom_dia.mp3")


# ---------- AGENDA ----------
def obter_agenda():
    """Devolve [(inicio, titulo, dia_inteiro), ...] dos eventos de hoje, por ordem."""
    resposta = requests.get(URL_AGENDA, timeout=15)
    calendario = icalendar.Calendar.from_ical(resposta.content)
    eventos = []
    for ev in recurring_ical_events.of(calendario).at(date.today()):
        titulo = str(ev.get("SUMMARY", "Sem título"))
        inicio = ev["DTSTART"].dt
        if isinstance(inicio, datetime):
            if inicio.tzinfo:
                inicio = inicio.astimezone(FUSO)
            eventos.append((inicio.replace(tzinfo=None), titulo, False))
        else:  # evento de dia inteiro (vem como date, sem hora)
            eventos.append((datetime.combine(inicio, datetime.min.time()), titulo, True))
    eventos.sort()
    return eventos


def frase_agenda(eventos):
    if not eventos:
        return "Hoje não tens nada na agenda."
    partes = []
    for inicio, titulo, dia_inteiro in eventos:
        if dia_inteiro:
            partes.append(f"{titulo}, o dia todo")
        elif inicio.minute:
            partes.append(f"{titulo} às {inicio.hour} horas e {inicio.minute} minutos")
        else:
            partes.append(f"{titulo} às {inicio.hour} horas")
    return "Na agenda de hoje tens: " + "; ".join(partes) + "."


# ---------- CLIMA ----------
def agrupar_horas(horas):
    """[9, 10, 11, 15] -> [[9, 11], [15, 15]]"""
    intervalos = []
    for h in horas:
        if intervalos and h == intervalos[-1][1] + 1:
            intervalos[-1][1] = h
        else:
            intervalos.append([h, h])
    return intervalos


def obter_clima():
    resposta = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": LAT,
            "longitude": LON,
            "daily": "temperature_2m_max,temperature_2m_min",
            "hourly": "precipitation_probability",
            "timezone": "Europe/Lisbon",
            "forecast_days": 1,
        },
        timeout=10,
    )
    dados = resposta.json()
    minimo = dados["daily"]["temperature_2m_min"][0]
    maximo = dados["daily"]["temperature_2m_max"][0]
    horas_chuva = [
        hora
        for hora, prob in enumerate(dados["hourly"]["precipitation_probability"])
        if prob is not None and prob >= LIMITE_CHUVA and HORA_INICIO <= hora <= HORA_FIM
    ]
    return minimo, maximo, agrupar_horas(horas_chuva)


def frase_chuva(intervalos):
    if not intervalos:
        return "Não deve chover durante o dia."
    partes = [f"das {ini} às {fim + 1} horas" for ini, fim in intervalos]
    return "Deve chover " + " e ".join(partes) + "."


# ---------- A "CHAMADA" ----------
# Ficheiro do toque (está na mesma pasta do script).
TOQUE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "freesound_community-rotary-phone-ring-medium-103869.mp3",
)
mci = ctypes.windll.winmm.mciSendStringW  # "comandos de texto" do Windows para tocar áudio


class Toque:
    """Toca o mp3 em loop. Se o ficheiro não existir, usa um som do sistema."""

    def __init__(self):
        self.usa_mp3 = os.path.exists(TOQUE)
        self.ultimo_bip = 0  # só usado no som do sistema

    def comecar(self):
        if self.usa_mp3:
            mci("close toque", None, 0, None)  # por segurança, caso tenha ficado aberto
            mci(f'open "{TOQUE}" type mpegvideo alias toque', None, 0, None)
            mci("play toque", None, 0, None)  # sem "wait": toca em fundo
        else:
            self.repetir_se_acabou()

    def repetir_se_acabou(self):
        """Chamada de tempos a tempos: se o som acabou, volta a tocá-lo."""
        if self.usa_mp3:
            estado = ctypes.create_unicode_buffer(32)  # sítio onde o Windows escreve a resposta
            mci("status toque mode", estado, 32, None)
            if estado.value == "stopped":  # chegou ao fim
                mci("play toque from 0", None, 0, None)
        elif time.time() - self.ultimo_bip >= 2:  # som do sistema: um "toque" a cada 2 s
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
            self.ultimo_bip = time.time()

    def parar(self):
        if self.usa_mp3:
            mci("stop toque", None, 0, None)
            mci("close toque", None, 0, None)
        else:
            winsound.PlaySound(None, 0)  # cala qualquer som do winsound


# Cores da janela (tema escuro, estilo chamada de telemóvel)
COR_FUNDO = "#1c1c1e"
COR_TEXTO_SECUNDARIO = "#a1a1a6"
COR_VERDE, COR_VERDE_HOVER = "#34c759", "#2ca94b"
COR_VERMELHO, COR_VERMELHO_HOVER = "#ff3b30", "#d93229"


def uma_chamada():
    """Devolve True (atendeu), False (recusou) ou None (ninguém atendeu)."""
    resposta = {"valor": None}
    inicio = time.time()

    ctk.set_appearance_mode("dark")
    janela = ctk.CTk(fg_color=COR_FUNDO)
    janela.title("Chamada")
    janela.resizable(False, False)
    janela.attributes("-topmost", True)  # sempre à frente

    largura, altura = 360, 520
    janela.geometry(f"{largura}x{altura}")  # o customtkinter ajusta à escala do Windows

    # --- Conteúdo ---
    # "Fotografia" do contacto: um círculo cinzento com uma letra no meio
    circulo = ctk.CTkFrame(janela, width=110, height=110, corner_radius=55, fg_color="#3a3a3c")
    circulo.pack(pady=(50, 20))
    ctk.CTkLabel(circulo, text="B", text_color="white", font=("Segoe UI", 48, "bold")).place(
        relx=0.5, rely=0.5, anchor="center"
    )

    ctk.CTkLabel(janela, text="Bom dia", font=("Segoe UI", 34, "bold"), text_color="white").pack()

    estado = ctk.CTkLabel(janela, text="a ligar...", font=("Segoe UI", 16), text_color=COR_TEXTO_SECUNDARIO)
    estado.pack(pady=(4, 0))

    ctk.CTkLabel(
        janela, text="Posso passar o resumo de hoje?", font=("Segoe UI", 18),
        text_color="white", wraplength=300, justify="center",
    ).pack(pady=(30, 0))

    # --- Botões redondos (Recusar à esquerda, Atender à direita, como no iPhone) ---
    zona_botoes = ctk.CTkFrame(janela, fg_color="transparent")
    zona_botoes.pack(side="bottom", pady=(0, 45))

    def responder(valor):
        resposta["valor"] = valor
        janela.quit()  # sai do mainloop; a janela é destruída mais abaixo

    def clicou(valor):
        # Ignora cliques no 1.º segundo: se a janela aparecer debaixo do rato
        # enquanto estás a clicar noutra coisa, não conta como resposta.
        if time.time() - inicio >= 1:
            responder(valor)

    def botao(coluna, texto, cor, cor_hover, valor):
        ctk.CTkButton(
            zona_botoes, text="", width=76, height=76, corner_radius=38,
            fg_color=cor, hover_color=cor_hover, command=lambda: clicou(valor),
        ).grid(row=0, column=coluna, padx=40)
        ctk.CTkLabel(zona_botoes, text=texto, font=("Segoe UI", 14), text_color="white").grid(
            row=1, column=coluna, pady=(8, 0)
        )

    botao(0, "Recusar", COR_VERMELHO, COR_VERMELHO_HOVER, False)
    botao(1, "Atender", COR_VERDE, COR_VERDE_HOVER, True)

    janela.protocol("WM_DELETE_WINDOW", lambda: responder(False))  # fechar no X = recusar

    # Centrar no ecrã. Com escala do Windows (ex.: 150%) o tkinter dá o tamanho do ecrã
    # "encolhido", por isso pedimos o tamanho real em pixels ao próprio Windows.
    ecra_largura = ctypes.windll.user32.GetSystemMetrics(0)
    ecra_altura = ctypes.windll.user32.GetSystemMetrics(1)
    escala = ecra_largura / janela.winfo_screenwidth()  # ex.: 1920 / 1280 = 1.5
    x = int((ecra_largura - largura * escala) / 2)
    y = int((ecra_altura - altura * escala) / 2)
    janela.wm_geometry(f"+{x}+{y}")  # wm_geometry muda só a posição, sem voltar a aplicar a escala

    # --- Toque + temporizador ---
    toque = Toque()
    pontos = ["a ligar.", "a ligar..", "a ligar..."]

    def verificar():
        """Corre a cada 0,3 s: repete o toque, anima o texto e vê se o tempo acabou."""
        if time.time() - inicio >= SEGUNDOS_A_TOCAR:
            janela.quit()  # ninguém atendeu -> resposta fica None
            return
        toque.repetir_se_acabou()
        estado.configure(text=pontos[int(time.time() * 2) % 3])
        janela.after(300, verificar)  # volta a correr daqui a 0,3 s

    try:
        toque.comecar()
        verificar()
        janela.mainloop()
    finally:
        # Isto corre SEMPRE (atendeu, recusou, tempo acabou ou até erro): o som para.
        toque.parar()
        # Cancela tarefas agendadas (as nossas e as internas do customtkinter),
        # senão aparecem avisos "invalid command name" depois de fechar.
        # (cancelamos direto no Tcl: o after_cancel também apagava os comandos,
        #  e depois o destroy() rebentava ao tentar apagá-los outra vez)
        for tarefa in janela.tk.call("after", "info"):
            janela.tk.call("after", "cancel", tarefa)
        janela.destroy()
    return resposta["valor"]


def ligar():
    for tentativa in range(TENTATIVAS):
        resposta = uma_chamada()
        if resposta is not None:
            return resposta
        if tentativa < TENTATIVAS - 1:
            time.sleep(PAUSA_ENTRE_CHAMADAS)
    return False


# ---------- VOZ ----------
def falar(texto):
    gTTS(texto, lang="pt", tld="pt").save(FICHEIRO)
    mci = ctypes.windll.winmm.mciSendStringW
    mci(f'open "{FICHEIRO}" type mpegvideo alias voz', None, 0, None)
    mci("play voz wait", None, 0, None)
    mci("close voz", None, 0, None)


# ---------- PROGRAMA ----------
if __name__ == "__main__":
    if ligar():
        try:
            agenda = frase_agenda(obter_agenda())
        except Exception as erro:
            print("Erro na agenda:", erro)
            agenda = "Não consegui ler a agenda."

        try:
            minimo, maximo, intervalos = obter_clima()
            clima = (
                f"A temperatura vai de {minimo:.0f} a {maximo:.0f} graus. "
                + frase_chuva(intervalos)
            )
        except Exception as erro:
            print("Erro no clima:", erro)
            clima = "Não consegui obter a previsão do tempo."

        texto = f"Bom dia, Júlia. {agenda} {clima}"
        print(texto)
        falar(texto)