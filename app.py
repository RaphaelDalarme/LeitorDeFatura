import os
import re
import json
import pdfplumber
from collections import defaultdict
from flask import Flask, render_template, request
from google import genai
from dotenv import load_dotenv
import markdown

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__)
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

CATEGORIAS = {
    "transporte": [
        "uber", "99", "cabify", "taxi", "metrô", "ônibus", "bus", "trem", "vlt", "estacionamento", "dutra drive"
    ],
    "assinaturas": [
        "spotify", "netflix", "prime", "deezer", "globoplay", "hbo", "disney", "paramount", "star+", "apple tv", "youtube premium", "playstation", "google microfun"
    ],
    "lazer_entretenimento": [
        "cinema", "teatro", "show", "parque", "zoo", "aquário", "museu", "balada", "bar", "pub", "karaokê", "cinemark", "hippo dino"
    ],
    "alimentacao": [
        "ifood", "uber eats", "rappi", "restaurante", "lanchonete", "padaria", "mercado", "supermercado", "mercearia", "burger king", "burguer king", "mcdonalds", "outback", "pizzaria", "pastelaria", "carnes", "nobreza"
    ],
    "compras_ecommerce": [
        "amazon", "mercadolivre", "americanas", "submarino", "magalu", "shoptime", "casas bahia", "carrefour", "extra", "ponto frio", "besni", "shopee", "bellaluna", "papelaria", "bbook"
    ],
    "saude_fitness": [
        "farmácia", "farmacia", "farma", "drogaria", "academia", "nutricionista", "psicólogo", "personal trainer", "medicina", "hospital", "clínica", "heartfit", "qualidoc"
    ]
}

PADRAO_INTER = r"^(\d{2}\s+de\s+[a-z]{3}\.\s+\d{4})\s+(.*?)\s+-\s*(\+)?\s*R\$\s*([\d\.,]+)$"
PADRAO_NUBANK = r"^(\d{2}\s+[A-Z]{3})\s+(?:\d{4}\s+)?(.*?)\s+(-)?\s*R\$\s*([\d\.,]+)$"

PADRAO_ITAU = r"^(\d{2}/\d{2})\s+([A-Za-z0-9\*\.\s\-\/]{3,50}?)\s+([\d]{1,3}(?:\.[\d]{3})*,[\d]{2})$"

TERMOS_IGNORAR = [
    "total", "pagamento", "saldo", "mensalidade", "anuidade", 
    "próxima fatura", "demais faturas", "limite", "encargos",
    "resumo", "demonstrativo", "subtotal", "crédito", "anterior"
]


def processar_pdf(caminho_pdf):
    compras = []
    totais_por_categoria = defaultdict(float)
    total_geral = 0.0

    with pdfplumber.open(caminho_pdf) as pdf:
        texto_completo = ""
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texto_completo += t + "\n"

    texto_limpo = re.sub(r'R\$\s*\n\s*', 'R$ ', texto_completo)

    ignorar_bloco = False

    for linha in texto_limpo.split("\n"):
        linha_str = linha.strip()
        if not linha_str:
            continue

        linha_lower = linha_str.lower()

        if "compras parceladas - próximas faturas" in linha_lower or "resumo da fatura" in linha_lower:
            ignorar_bloco = True
            continue

        if ignorar_bloco and "lançamentos:" in linha_lower:
            ignorar_bloco = False

        if ignorar_bloco:
            continue

        data, descricao, eh_pagamento, valor_texto = None, None, False, None

        match_inter = re.search(PADRAO_INTER, linha_str, re.IGNORECASE)
        match_nubank = re.search(PADRAO_NUBANK, linha_str)
        match_itau = re.search(PADRAO_ITAU, linha_str)

        if match_inter:
            data = match_inter.group(1)
            descricao = match_inter.group(2).strip()
            eh_pagamento = match_inter.group(3) == '+'
            valor_texto = match_inter.group(4)
        elif match_nubank:
            data = match_nubank.group(1)
            descricao = match_nubank.group(2).strip()
            eh_pagamento = match_nubank.group(3) == '-'
            valor_texto = match_nubank.group(4)
        elif match_itau:
            data = match_itau.group(1)
            descricao = match_itau.group(2).strip()
            valor_texto = match_itau.group(3)

        if data and valor_texto:
            desc_lower = descricao.lower()

            if any(termo in desc_lower for termo in TERMOS_IGNORAR):
                continue

            valor_limpo = valor_texto.replace('.', '').replace(',', '.')
            try:
                valor_float = float(valor_limpo)
            except ValueError:
                valor_float = 0.0

            if valor_float > 0 and not eh_pagamento:
                desc_limpa = re.sub(r'\(.*?\)', '', descricao)
                desc_limpa = re.sub(r'^(DL|EBN|MP|ASAAS|MLP|PB)\s*\*?\s*', '', desc_limpa, flags=re.IGNORECASE)
                desc_busca = desc_limpa.lower().strip()

                cat_encontrada = "Outros"
                for nome_cat, palavras in CATEGORIAS.items():
                    if any(p in desc_busca for p in palavras):
                        cat_encontrada = nome_cat
                        break

                item = {
                    "data": data,
                    "descricao": desc_limpa.strip(),
                    "valor": valor_float,
                    "categoria": cat_encontrada.replace('_', ' ').capitalize()
                }
                compras.append(item)
                totais_por_categoria[item["categoria"]] += valor_float
                total_geral += valor_float

    return compras, dict(totais_por_categoria), total_geral


def gerar_insights_gemini(totais, total_geral):
    if not GEMINI_API_KEY:
        return "Erro: Chave da API do Gemini não foi encontrada no arquivo .env."

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = f"""
        Você é um consultor financeiro pessoal especialista em finanças comportamentais.
        Analise os dados desta fatura de cartão de crédito e forneça de 3 a 4 conselhos práticos e diretos de como economizar.

        - Total Geral da Fatura: R$ {total_geral:.2f}
        - Resumo de Gastos por Categoria: {json.dumps(totais, ensure_ascii=False)}

        Instruções de Formatação (MUITO IMPORTANTE):
        - Separe CADA conselho em um parágrafo diferente com duas quebras de linha.
        - Use listas numeradas bem espaçadas (1., 2., 3., 4.).
        - Deixe títulos e valores importantes em negrito.
        - Não coloque todo o texto em um bloco contínuo.
        """

        response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"Não foi possível gerar os insights no momento. Erro: {str(e)}"


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        file = request.files.get('fatura')
        
        if not file or not file.filename.lower().endswith('.pdf'):
            return render_template('index.html', compras=None, erro="Por favor, envie um arquivo válido no formato PDF.")

        caminho = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(caminho)

        try:
            compras, totais, total_geral = processar_pdf(caminho)
        except Exception as e:
            compras = []

        if os.path.exists(caminho):
            os.remove(caminho)

        if not compras:
            return render_template(
                'index.html', 
                compras=None, 
                erro="Não foi possível ler as transações deste PDF. Verifique se o documento é uma fatura compatível."
            )

        insight_bruto = gerar_insights_gemini(totais, total_geral)
        insight_ia = markdown.markdown(insight_bruto)

        labels_grafico = json.dumps(list(totais.keys()))
        valores_grafico = json.dumps(list(totais.values()))

        return render_template(
            'index.html',
            compras=compras,
            totais=totais,
            total_geral=total_geral,
            insight_ia=insight_ia,
            labels_grafico=labels_grafico,
            valores_grafico=valores_grafico
        )

    return render_template('index.html', compras=None)

if __name__ == '__main__':
    app.run(debug=True)