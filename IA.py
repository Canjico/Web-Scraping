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
  {{"campo": "estado", "valor": "BR", "motivo": "UF não encontrada claramente no texto; foi usado fallback"}}
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

        Compatibilidade com scripts antigos:
        - Se o conteúdo total for pequeno, manda em uma chamada.
        - Se for grande, faz leitura progressiva em blocos de 50k (analisar_smart).
        """
        if not textos:
            return {"aprovado": False, "motivo_aprovacao": "Nenhum texto extraído.", "incertezas": []}

        texto_total = "\n\n--- NOVO DOCUMENTO ---\n\n".join([t for t in textos if t])

        # Se passou do limite confortável, usa o modo smart (progressivo)
        if len(texto_total) > 50000:
            return self.analisar_smart(texto_total, contexto=contexto, tamanho_chunk=50000)

        return self._consultar_gpt_core(texto_total, contexto)
    def analisar_smart(self, texto_completo: str, contexto: str, tamanho_chunk: int = 50000) -> dict:
        """
        Lê o edital em blocos (chunks) de forma progressiva (padrão: 50k caracteres).

        Estratégia:
        - Sempre inclui um "cabeçalho" (primeiros ~10k chars) junto com o trecho atual,
          para não perder metadados que costumam estar no início (título, instituição, etc).
        - Continua lendo enquanto a IA indicar que o prazo/fluxo não foi encontrado no trecho atual.
        - Para assim que:
          - Aprovar (data válida >= hoje ou fluxo contínuo), OU
          - Reprovar de forma definitiva (ex: prazo já expirou).
        """
        if not texto_completo or not texto_completo.strip():
            return {"aprovado": False, "motivo_aprovacao": "Texto vazio.", "incertezas": []}

        tamanho_chunk = int(tamanho_chunk or 50000)
        if tamanho_chunk < 10000:
            tamanho_chunk = 10000

        header = texto_completo[:10000]

        chunks = [texto_completo[i:i + tamanho_chunk] for i in range(0, len(texto_completo), tamanho_chunk)]
        ultimo_resultado = {"aprovado": False, "motivo_aprovacao": "Análise iniciada", "incertezas": []}

        logger.info(f"🧠 Iniciando análise Smart em {len(chunks)} blocos. Contexto: {contexto}")

        for i, chunk in enumerate(chunks, start=1):
            logger.info(f"🔍 Analisando parte {i} de {len(chunks)}...")

            texto_para_ia = (
                f"--- CABEÇALHO (INÍCIO DO DOCUMENTO) ---\n{header}"
                f"\n\n--- TRECHO ATUAL {i}/{len(chunks)} ---\n{chunk}"
            )

            resultado = self._consultar_gpt_core(texto_para_ia, contexto)
            ultimo_resultado = resultado or ultimo_resultado

            aprovado = resultado.get("aprovado") if isinstance(resultado, dict) else None
            motivo = (resultado.get("motivo_aprovacao") or "").strip() if isinstance(resultado, dict) else ""

            # ✅ aprovou => encerra
            if aprovado is True:
                logger.info(f"✅ Decisão POSITIVA tomada na parte {i}. Interrompendo leitura.")
                return resultado

            # ❌ reprovou, mas pode ser "não encontrei ainda" => continua
            motivo_lower = motivo.lower()
            nao_encontrou_prazo = "prazo indefinido" in motivo_lower or "não encontrado no trecho atual" in motivo_lower

            if aprovado is False and nao_encontrou_prazo and i < len(chunks):
                logger.info(f"⏳ Parte {i} sem prazo/fluxo claro. Lendo próxima...")
                continue

            # ❌ reprovou definitivo (ou acabou texto)
            logger.info(f"🏁 Decisão tomada na parte {i}: {motivo or 'reprovado/sem dados'}.")
            return resultado

        logger.info("🏁 Leitura finalizada. Resultado final mantido.")
        return ultimo_resultado


    def extrair_dados(self, texto: str, contexto: str, url: str) -> dict:
        return {}

# 👇 export único e estável
ia_client = IAClient()
