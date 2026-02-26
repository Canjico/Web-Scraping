# IA.py
import json
import re
import datetime
from .utils import get_openai_client, logger


def _extrair_json_seguro(texto: str) -> dict:
    """
    Extrai JSON válido de uma resposta da IA.
    Lida com texto explicativo, markdown e lixo antes/depois.
    """
    if not texto or not texto.strip():
        return {}

    texto = texto.strip()

    # Remove blocos markdown ```json
    if "```" in texto:
        partes = texto.split("```")
        for p in partes:
            p = p.strip()
            if p.startswith("{") or p.startswith("["):
                texto = p
                break

    # Regex fallback: pega o primeiro JSON válido
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if match:
        texto = match.group(0)

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        logger.error(f"Falha ao decodificar JSON da IA. Texto recebido: {texto[:100]}...")
        return {}


class IAClient:
    def __init__(self):
        self.client = get_openai_client()

    def _consultar_gpt_core(self, texto_acumulado: str, contexto: str) -> dict:
        """
        Método interno que monta o prompt e chama a API.
        """
        hoje_str = datetime.date.today().strftime("%Y-%m-%d")

        prompt = f"""
RESPONDA EXCLUSIVAMENTE EM JSON VÁLIDO.
NÃO escreva explicações fora do JSON.
NÃO use markdown.

Data de hoje para referência: {hoje_str}

Analise o texto parcial ou completo do edital abaixo.

REGRAS DE EXTRAÇÃO:
- Identifique corretamente a sigla do Estado (UF) da instituição ou edital (ex: SP, PR, MG, AM).
- Se for uma instituição federal ou de abrangência nacional (ex: CNPq, FINEP, CAPES), use "BR" no campo estado.

REGRAS DE INCERTEZA (OBRIGATÓRIO):
- Sempre preencha o campo "incertezas" (lista). Se não houver nenhuma, retorne [] (lista vazia).
- Se você NÃO tiver 100% de certeza de algum campo que preencheu (ex: estado, data_final_submissao, valor_total, publico_alvo), adicione um item em "incertezas" no formato:
  {"campo": "estado", "valor": "BR", "motivo": "UF não encontrada claramente no texto; foi usado fallback"}
- Se você colocou "BR" por não conseguir identificar a UF e NÃO houver evidência clara de que o edital é nacional/federal, isso DEVE virar incerteza.
- Se você precisar usar "Indefinido" por falta de informação clara, isso DEVE virar incerteza.

REGRAS DE APROVAÇÃO:
- O edital só é APROVADO se:
  1. For identificado como "Fluxo Contínuo".
  2. OU possuir uma data final de submissão IGUAL ou POSTERIOR à data de hoje ({hoje_str}).
- Se a data de submissão já passou, aprovado = false.
- Se não houver data clara e não for fluxo contínuo, aprovado = false (motivo: "Prazo indefinido ou não encontrado no trecho atual").

REGRAS PARA TAGS (MUITO IMPORTANTE):
- O campo "tags" deve conter APENAS valores EXATOS da lista abaixo que melhor se aplicam ao edital.
- NUNCA invente tags novas. Use apenas estas:
  - Governo & Políticas Públicas
  - Administração & Gestão
  - Tecnologia & Inovação
  - Educação & Capacitação
  - Saúde & Bem-Estar
  - Meio Ambiente & Sustentabilidade
  - Agricultura & Desenvolvimento Rural
  - Infraestrutura, Obras & Engenharia
  - Logística, Suprimentos & Operações
  - Economia, Finanças & Licitações
  - Ciência, Pesquisa & Desenvolvimento
  - Social, Direitos & Comunidade
  - Cultura, Arte & Patrimônio
  - Esporte, Lazer & Qualidade de Vida
  - Cooperação & Relações Internacionais

Formato obrigatório do JSON de resposta:
{{
  "aprovado": true|false,
  "motivo_aprovacao": "Explique brevemente por que foi aprovado (ex: 'Prazo até 10/12/2025') ou reprovado.",
  "titulo": "Título curto e claro do Edital",
  "instituicao": "Nome da Instituição (ex: FAPESP, CNPq)",
  "estado": "Sigla UF (ex: SP, PR, BR)",
  "icone_instituicao": "/icons/default.png",
  "valor_total": "Ex: R$ 5.000.000,00 ou Indefinido",
  "objetivo": "Resumo do objetivo em 1 frase",
  "publico_alvo": "Ex: Pesquisadores, Empresas, etc",
  "data_final_submissao": "YYYY-MM-DD" ou "Fluxo Contínuo" ou "Indefinido",
  "fluxo_continuo": true|false,
  "tags": ["Tecnologia & Inovação", "Ciência, Pesquisa & Desenvolvimento"],
  "incertezas": []
}}

Contexto/Título do Bloco:
{contexto}

Conteúdo do Documento (pode ser parcial):
{texto_acumulado}
"""

        try:
            resp = self.client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "Você é um analista sênior de editais de fomento público."},
                    {"role": "user", "content": prompt}
                ],
            )

            raw = resp.choices[0].message.content
            dados = _extrair_json_seguro(raw)
            
            if "aprovado" not in dados:
                dados["aprovado"] = False
                dados["motivo_aprovacao"] = "IA não retornou campo 'aprovado'."

            return dados

        except Exception as e:
            logger.error(f"Erro crítico na análise IA: {e}")
            return {
                "aprovado": False,
                "motivo_aprovacao": f"Erro técnico na análise IA: {str(e)}"
            }

    def analisar_edital_completo(self, textos: list, contexto: str) -> dict:
        """
        Método LEGADO (Wrapper). 
        Junta tudo e manda de uma vez (truncado em 50k para segurança simples).
        Recomendado usar 'analisar_smart' para documentos grandes.
        """
        if not textos:
            return {"aprovado": False, "motivo_aprovacao": "Nenhum texto extraído."}

        texto_total = "\n\n--- NOVO DOCUMENTO ---\n\n".join(textos)
        # Truncamento simples para evitar crash se não usar o smart
        return self._consultar_gpt_core(texto_total[:50000], contexto)

    def analisar_smart(self, texto_completo: str, contexto: str) -> dict:
        """
        Lê o edital em blocos (chunks) de forma progressiva.
        Para assim que encontrar informações suficientes para APROVAÇÃO.
        Caso contrário, continua lendo até o fim.
        """
        TAMANHO_CHUNK = 40000 
        # 40k chars ~= 10-15k tokens. Seguro para acumular algumas passadas.
        
        if not texto_completo or not texto_completo.strip():
             return {"aprovado": False, "motivo_aprovacao": "Texto vazio."}

        # Dividir em blocos
        chunks = [texto_completo[i:i+TAMANHO_CHUNK] for i in range(0, len(texto_completo), TAMANHO_CHUNK)]
        
        historico_texto = ""
        ultimo_resultado = {"aprovado": False, "motivo_aprovacao": "Análise iniciada"}

        logger.info(f"🧠 Iniciando análise Smart em {len(chunks)} blocos. Contexto: {contexto}")

        for i, chunk in enumerate(chunks, 1):
            historico_texto += f"\n\n--- PARTE {i}/{len(chunks)} ---\n{chunk}"
            
            logger.info(f"🔍 Analisando parte {i} de {len(chunks)}...")
            
            resultado = self._consultar_gpt_core(historico_texto, contexto)
            ultimo_resultado = resultado
            
            # CRITÉRIO DE PARADA: Sucesso
            # Se a IA já aprovou (achou data válida ou fluxo contínuo), paramos de ler.
            if resultado.get("aprovado") is True:
                logger.info(f"✅ Decisão POSITIVA tomada na parte {i}. Interrompendo leitura.")
                return resultado
            
            # Se reprovou, verificamos se é definitivo ou se falta info.
            # Mas a estratégia mais segura é: Se não aprovou, continua lendo até achar a data.
            # Só paramos no final se nunca aprovar.
            logger.info(f"⏳ Parte {i} não conclusiva ou reprovada ({resultado.get('motivo_aprovacao')}). Lendo próxima...")

        logger.info("🏁 Leitura finalizada. Resultado final mantido.")
        return ultimo_resultado

    def extrair_dados(self, texto: str, contexto: str, url: str) -> dict:
        return {}

# 👇 export único e estável
ia_client = IAClient()
