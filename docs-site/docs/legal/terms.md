# Termos de Serviço

**Última atualização:** 30 de abril de 2026

Estes Termos de Serviço ("Termos") regem o uso da plataforma **TELECOM TOWER POWER** (a "Plataforma"), operada pela **TELECOM TOWER POWER LTDA.** (CNPJ a publicar), com sede em Brasília – DF, Brasil ("nós", "nosso").

Ao criar uma conta, gerar uma chave de API ou efetuar qualquer pagamento, você ("Cliente") concorda com estes Termos. Se não concordar, não utilize a Plataforma.

---

## 1. Objeto do contrato

A Plataforma fornece, via API REST, dados públicos de torres de telecomunicações da Anatel, perfis de elevação SRTM, cálculos de propagação de rádio (FSPL + correção de terreno + Fresnel) e relatórios PDF. O serviço é fornecido **"como está"**, sem garantias de adequação a finalidade específica para além daquelas estabelecidas pelo Código de Defesa do Consumidor (Lei 8.078/90) e pelo Marco Civil da Internet (Lei 12.965/14).

## 2. Cadastro e segurança da chave de API

2.1. O Cliente é responsável por manter a confidencialidade de sua chave de API e por toda atividade realizada com ela.

2.2. Em caso de comprometimento, o Cliente deve solicitar a rotação imediata em [support@telecomtowerpower.com.br](mailto:support@telecomtowerpower.com.br).

2.3. Reservamo-nos o direito de revogar chaves usadas para abuso, scraping não autorizado, ataques DoS ou violação destes Termos.

## 3. Planos e pagamento

3.1. Os planos vigentes (Free, Starter, Pro, Business, Enterprise) e respectivos preços estão publicados em [https://app.telecomtowerpower.com.br/pricing](https://app.telecomtowerpower.com.br/pricing).

3.2. Pagamentos são processados pela **Stripe**. Não armazenamos números de cartão; a Stripe é PCI-DSS Nível 1.

3.3. Faturamento mensal renova automaticamente. Faturamento anual é pré-pago e não-reembolsável após o 7º dia (vide §5).

3.4. Reajustes anuais limitados ao IPCA acumulado dos últimos 12 meses, com aviso prévio de 30 dias.

## 4. Limites e uso aceitável

4.1. Cada plano define um limite de chamadas/minuto, número de torres por consulta e cota mensal de PDFs. Excessos podem ser negados (HTTP 429) sem reembolso.

4.2. **É proibido**: (a) revender o serviço sem acordo escrito; (b) usar para fins ilícitos; (c) tentar contornar os limites de taxa via múltiplas contas; (d) violar direitos de terceiros.

## 5. Cancelamento e reembolso

5.1. **Direito de arrependimento (CDC art. 49):** o Cliente pode cancelar em até **7 dias corridos** após a primeira contratação ou upgrade, com reembolso integral.

5.2. Após esse prazo, planos mensais podem ser cancelados a qualquer momento; o serviço permanece ativo até o fim do ciclo já pago, sem reembolso proporcional.

5.3. Planos anuais não são reembolsáveis após o prazo de 7 dias, salvo descumprimento de SLA documentado por nós.

5.4. Detalhes na [Política de Reembolso](refund-policy.md).

## 6. Disponibilidade e SLA

6.1. Esforço razoável de disponibilidade ≥ 99,5% (planos Free–Business). Plano Enterprise: SLA contratual ≥ 99,95%.

6.2. Status público: [https://docs.telecomtowerpower.com.br/operations/status/](../operations/status.md). Janelas de manutenção comunicadas com 24h de antecedência.

## 7. Limitação de responsabilidade

7.1. Os cálculos de propagação são **estimativas baseadas em dados públicos** (ANATEL/SRTM) e não substituem medições de campo. **O Cliente é o único responsável** pelas decisões de engenharia e regulatórias derivadas do uso.

7.2. Nossa responsabilidade total agregada limita-se ao **valor pago pelo Cliente nos 12 meses anteriores ao evento**, exceto em casos de dolo ou culpa grave.

## 8. Propriedade intelectual

8.1. Os dados de torres da ANATEL são públicos. Os relatórios gerados pela Plataforma podem ser livremente usados e redistribuídos pelo Cliente.

8.2. Modelos de propagação, código e marca são de nossa propriedade. Engenharia reversa é proibida.

## 9. Privacidade

Tratamento de dados pessoais conforme nossa [Política de Privacidade](privacy.md), em conformidade com a LGPD (Lei 13.709/18).

## 10. Foro e legislação aplicável

10.1. Estes Termos são regidos pela legislação brasileira.

10.2. Foro eleito: Brasília – DF, Brasil.

## 11. Contato

- **Suporte:** [support@telecomtowerpower.com.br](mailto:support@telecomtowerpower.com.br)
- **Encarregado LGPD (DPO):** [dpo@telecomtowerpower.com.br](mailto:dpo@telecomtowerpower.com.br)
- **Vendas:** [sales@telecomtowerpower.com.br](mailto:sales@telecomtowerpower.com.br)
