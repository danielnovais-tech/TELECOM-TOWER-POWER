# Política de Privacidade

**Última atualização:** 30 de abril de 2026

A **TELECOM TOWER POWER LTDA.** ("nós") trata dados pessoais em conformidade com a Lei Geral de Proteção de Dados (LGPD – Lei 13.709/2018) e o Marco Civil da Internet.

---

## 1. Quem é o controlador

- **Razão social:** TELECOM TOWER POWER LTDA.
- **Sede:** Brasília – DF, Brasil
- **Encarregado de Dados (DPO):** [dpo@telecomtowerpower.com.br](mailto:dpo@telecomtowerpower.com.br)

## 2. Quais dados coletamos

| Categoria | Dados | Finalidade | Base legal (LGPD art. 7º) |
|---|---|---|---|
| Cadastro | E-mail, nome (opcional) | Provisão de chave de API, faturamento | Execução de contrato (V) |
| Pagamento | Tokenizado pela Stripe (não armazenamos PAN) | Cobrança | Execução de contrato (V) |
| Uso da API | IP, User-Agent, endpoint, timestamp, status | Anti-fraude, capacity planning, segurança | Legítimo interesse (IX) |
| Logs de auditoria | Emissão de chave, login SSO, ações administrativas | Conformidade SOC 2, resposta a incidentes | Cumprimento de obrigação legal (II) |
| Comunicação | Tickets de suporte, e-mails | Atendimento | Execução de contrato (V) |

**Não coletamos dados sensíveis** (origem racial, saúde, biometria, opinião política).

## 3. Como usamos os dados

- Provisão e cobrança do serviço.
- Detecção de abuso (rate-limit, bot signup, fraude de cartão).
- Notificações operacionais (incidentes, fim de período de teste).
- Estatísticas agregadas (sem identificação) para roadmap.

**Não vendemos seus dados.** Não compartilhamos com terceiros para marketing.

## 4. Subprocessadores

Compartilhamos o estritamente necessário com:

| Subprocessador | Finalidade | País | Salvaguarda |
|---|---|---|---|
| Amazon Web Services (AWS) | Hosting, banco, S3 | Brasil (sa-east-1) + EUA (us-east-1) | DPA AWS, criptografia em trânsito/repouso |
| Stripe Payments Inc. | Processamento de cartão | EUA | DPA Stripe, PCI-DSS L1 |
| Anthropic / Amazon Bedrock | Modelos de IA (Claude) | EUA | Processamento sem retenção, prompts não usados para treino |
| Cloudflare | Anti-bot (Turnstile) e CDN | Global | DPA Cloudflare |
| Sentry / Grafana | Observabilidade | Brasil + UE | Dados pseudonimizados |
| AWS SES | E-mail transacional | EUA (us-east-1) | DPA AWS |

Lista completa e atualizada: a publicar em [docs.telecomtowerpower.com.br/legal/](./).

## 5. Transferência internacional

Algumas operações ocorrem fora do Brasil (EUA principalmente). Tais transferências obedecem ao art. 33 da LGPD: cláusulas contratuais padrão com cada subprocessador e/ou adequação reconhecida pela ANPD.

## 6. Retenção

| Dado | Retenção |
|---|---|
| Logs de API | 30 dias |
| Logs de auditoria | 7 anos (obrigação fiscal/SOC 2) |
| Dados de cadastro | Até cancelamento + 5 anos (CDC art. 27) |
| Backups criptografados | 14 dias (rolling) |

## 7. Seus direitos (LGPD art. 18)

Você pode, gratuitamente, solicitar:

1. Confirmação da existência de tratamento;
2. Acesso aos dados;
3. Correção de dados incompletos ou desatualizados;
4. Anonimização, bloqueio ou eliminação de dados desnecessários;
5. Portabilidade;
6. Eliminação dos dados tratados com seu consentimento;
7. Informação sobre compartilhamento;
8. Revogação do consentimento.

Envie a solicitação para [dpo@telecomtowerpower.com.br](mailto:dpo@telecomtowerpower.com.br). Resposta em até **15 dias**.

Você também pode reclamar diretamente à **ANPD** ([www.gov.br/anpd](https://www.gov.br/anpd)).

## 8. Segurança

- TLS 1.2+ obrigatório em todas as conexões.
- Criptografia em repouso (AWS KMS) para banco e S3.
- Chaves de API hashadas (SHA-256) — nunca armazenadas em claro.
- Rate-limit por chave e por IP; CAPTCHA no signup gratuito.
- Logs de auditoria imutáveis com IP + UA na emissão de chave.
- Backups criptografados, rotação de chaves trimestral.
- Programa SOC 2 Tipo II em andamento (gap analysis publicado em [/compliance/soc2/](../compliance/soc2/README.md)).

## 9. Cookies

A página de marketing e o portal usam apenas cookies estritamente necessários (sessão, CSRF). Não usamos cookies de publicidade.

## 10. Crianças

O serviço destina-se a empresas e profissionais. Não coletamos conscientemente dados de menores de 18 anos.

## 11. Alterações

Mudanças materiais serão comunicadas por e-mail com 30 dias de antecedência. Versões anteriores ficam disponíveis no histórico do repositório público de docs.

## 12. Contato

- **DPO:** [dpo@telecomtowerpower.com.br](mailto:dpo@telecomtowerpower.com.br)
- **Suporte:** [support@telecomtowerpower.com.br](mailto:support@telecomtowerpower.com.br)
- **Endereço postal:** a publicar (Brasília – DF)
