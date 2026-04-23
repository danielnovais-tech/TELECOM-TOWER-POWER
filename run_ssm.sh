export AWS_PAGER=""
CID=$(aws ssm send-command --region sa-east-1 --instance-ids i-045166a6a1933f507 --document-name AWS-RunShellScript --parameters 'commands=["echo HOST_FILE:; ls -la /home/ubuntu/TELECOM-TOWER-POWER/grafana/dashboards/ 2>&1; wc -c /home/ubuntu/TELECOM-TOWER-POWER/grafana/dashboards/telecom.json 2>&1; echo CONTAINER_FILE:; sudo docker exec telecom-tower-power-grafana-1 ls -la /var/lib/grafana/dashboards/ 2>&1; sudo docker exec telecom-tower-power-grafana-1 wc -c /var/lib/grafana/dashboards/telecom.json 2>&1; echo CONTAINER_HEAD:; sudo docker exec telecom-tower-power-grafana-1 head -c 100 /var/lib/grafana/dashboards/telecom.json 2>&1; echo; echo PROVISIONING_CONFIG:; sudo docker exec telecom-tower-power-grafana-1 cat /etc/grafana/provisioning/dashboards/dashboards.yaml 2>&1 || sudo docker exec telecom-tower-power-grafana-1 cat /etc/grafana/provisioning/dashboards/*.yaml 2>&1; echo VOLUMES:; sudo docker inspect telecom-tower-power-grafana-1 --format \"{{json .Mounts}}\" 2>&1"]' --query 'Command.CommandId' --output text)
echo "CID=$CID"
for i in {1..6}; do
  sleep 5
  STATUS=$(aws ssm get-command-invocation --region sa-east-1 --command-id $CID --instance-id i-045166a6a1933f507 --query Status --output text 2>/dev/null)
  echo "Status: $STATUS"
  if [[ "$STATUS" == "Success" || "$STATUS" == "Failed" ]]; then
    break
  fi
done
echo "===STDOUT==="
aws ssm get-command-invocation --region sa-east-1 --command-id $CID --instance-id i-045166a6a1933f507 --query StandardOutputContent --output text
echo "===STDERR==="
aws ssm get-command-invocation --region sa-east-1 --command-id $CID --instance-id i-045166a6a1933f507 --query StandardErrorContent --output text
