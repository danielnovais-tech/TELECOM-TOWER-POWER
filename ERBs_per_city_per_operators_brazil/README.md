# Base de Dados: ERBs por Cidade por Operadora - Brasil

## Descrição

Esta base de dados contém informações detalhadas sobre as Estações Rádio Base (ERBs) de telecomunicações distribuídas por cidades e operadoras no território brasileiro. Os dados foram obtidos através da **Lei de Acesso à Informação (LAI) - Lei nº 12.527/2011** junto à **Agência Nacional de Telecomunicações (Anatel)**.

## Fonte dos Dados

- **Órgão**: Agência Nacional de Telecomunicações (Anatel)
- **Método de Obtenção**: Lei de Acesso à Informação (LAI) - Lei nº 12.527/2011
- **Arquivo**: `ERBs_com_equipamentos_v2.xlsx`

## Estrutura dos Dados

A base de dados é composta pelas seguintes colunas:

| Campo | Descrição |
|-------|-----------|
| `NumCnpjCpf` | Número do CNPJ da empresa prestadora de serviços de telecomunicações |
| `Prestadora` | Nome da empresa operadora de telecomunicações |
| `NumEstacao` | Número identificador único da estação rádio base |
| `CodEquipamentoTransmissor` | Código do equipamento transmissor instalado na ERB |
| `Fabricante Agrupado` | Nome agrupado/padronizado do fabricante do equipamento |
| `Fabricante` | Nome completo oficial do fabricante do equipamento |
| `CN` | Código Nacional (conhecido popularmente de DDD) |
| `Município` | Nome do município onde a ERB está localizada |
| `UF` | Unidade Federativa (estado) onde a ERB está situada |

## Exemplo de Dados

```
NumCnpjCpf         Prestadora          NumEstacao  CodEquipamento  Fabricante    Município              UF
71208516000174     ALGAR TELECOM S/A   945005      007881401350    Nokia         São Joaquim da Barra   SP
71208516000174     ALGAR TELECOM S/A   945641      007881401350    Nokia         Ituverava              SP
71208516000174     ALGAR TELECOM S/A   946222      007881401350    Nokia         Orlândia               SP
```

## Possíveis Aplicações

Esta base de dados pode ser utilizada para:

1. **Análise de Cobertura**: Mapear a distribuição de ERBs por região
2. **Estudos de Mercado**: Analisar a presença de operadoras por município
3. **Pesquisa Tecnológica**: Identificar fabricantes e tecnologias utilizadas
4. **Planejamento Urbano**: Estudar a infraestrutura de telecomunicações
5. **Análise Regulatória**: Acompanhar o cumprimento de obrigações de cobertura

## Considerações Técnicas

- Os dados representam a infraestrutura física de telecomunicações licenciada pela Anatel
- Cada linha representa um equipamento transmissor específico em uma estação
- Uma mesma estação pode ter múltiplos equipamentos (diferentes códigos)
- Os dados refletem autorizações e licenças concedidas pela Anatel

## Lei de Acesso à Informação (LAI)

A Lei nº 12.527/2011 garante o direito constitucional de acesso às informações públicas. Esta base de dados foi disponibilizada em conformidade com os princípios de:

- **Transparência**: Publicidade como regra geral
- **Eficiência**: Facilidade de acesso às informações
- **Objetividade**: Dados claros e precisos

## Formato dos Dados

- **Tipo**: Planilha Microsoft Excel (.xlsx)
- **Codificação**: Texto em português brasileiro
- **Estrutura**: Tabular, uma linha por equipamento

## Limitações e Considerações

1. Os dados refletem o momento da consulta à Anatel
2. Podem existir ERBs não licenciadas ou em processo de licenciamento não incluídas
3. As informações estão sujeitas a alterações conforme atualizações regulatórias
4. É recomendável verificar a data de atualização dos dados

---

**Nota**: Este dataset representa uma importante fonte de informações sobre a infraestrutura de telecomunicações brasileira, obtida através dos mecanismos de transparência pública estabelecidos pela legislação nacional.