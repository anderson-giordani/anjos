# Dashboard ANJOS

Dashboard de Meta Ads + CRM (somente leads do Meta), com dados de **julho/2026 em diante**.

## Arquivos

| Arquivo | Para quê |
|---|---|
| `build_data.py` | Busca Meta (Graph API) e lê os negócios do Pipedrive; grava `data.json`. |
| `template.html` | Página do dashboard (menu, abas, pop-up, recomendações). |
| `build_html.py` | Injeta `data.json` no template e grava `dist/index.html`. |

## Atualização diária (rotina das 06:00)

1. Buscar os negócios do funil NACIONAL no Pipedrive com `getDeals`:
   `pipeline_id=1`, `sort_by=add_time`, `sort_direction=desc`, `limit=500`,
   `custom_fields` = as quatro chaves de UTM abaixo. Repetir com `cursor` até o último negócio ter `add_time` anterior a 2026-07-01.
   O resultado é grande e a ferramenta o salva em arquivo; use esses arquivos como `--deals`.
   - utm_source `d0c29b1b081a2613f86dda37adff5be306753787`
   - utm_medium `6f444efc2ee75fbbe4efb9b6ba0eff348a1bc4df`
   - utm_campaign `08b0d86f0657a3bc2f04de8091939ac23d12d55e`
   - utm_content `91bfef91619b3d5f91ded82c0aa17c4831300b33`
2. `python3 dashboard/build_data.py --deals <arquivos> --out dashboard/data.json`
   (usa a credencial do ambiente para `graph.facebook.com`; nenhum token fica no código).
3. `python3 dashboard/build_html.py`
4. Publicar `dashboard/dist/index.html` no mesmo artefato (ação `publish` com o `url` do artefato).

## Regras de negócio

- **Lead do Meta no CRM:** `utm_source` começa com `ig`, `fb`, `facebook`, `instagram` ou `meta`.
- **Leads (Gerenciador):** ação `lead` do Meta. É a base dos custos por etapa.
- **Etapas:** 1 Leads novos, 2 Tentativa, 3 Conectados, 4 MQL, 5 SQL, 6 1ª reunião realizada, 7 COF, 8 Fechamento.
  Um negócio "chegou" na etapa N se a etapa atual dele (ou a etapa em que foi perdido) é N ou posterior; ganho conta como etapa 8.
- **Custo por etapa:** investimento ÷ (leads do Gerenciador × taxa de avanço do CRM até a etapa).
- **Atribuição por anúncio:** `utm_content` ou `utm_campaign` igual ao ID ou ao nome do anúncio.
- **Comparação:** janela de mesmo tamanho imediatamente anterior (ex.: 30 dias vs. 30 dias anteriores).
- **Motivo de ganho:** não existe no Pipedrive; a tela mostra um aviso até existir um campo.
