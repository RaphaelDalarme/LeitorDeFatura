import pdfplumber

with pdfplumber.open("fatura_teste.pdf") as pdf:
    # A página 3 (índice 2) contém as primeiras compras
    pagina = pdf.pages[2]
    texto = pagina.extract_text()
    
    print("--- TEXTO EXTRAÍDO DA PÁGINA 3 ---")
    if texto:
        linhas = texto.split("\n")
        # Imprime as primeiras 15 linhas exatamente como vieram do PDF
        for i, linha in enumerate(linhas[:15]):
            print(f"Linha {i}: {repr(linha)}")
    else:
        print("A página retornou vazia!")