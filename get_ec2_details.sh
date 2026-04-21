#!/usr/bin/env bash
set -euo pipefail

echo "--- EC2 Instances ---"
aws ec2 describe-instances --region sa-east-1 --filters Name=instance-state-name,Values=running --query 'Reservations[].Instances[].{InstanceId:InstanceId,Name:Tags[?Key==`Name`]|[0].Value,PublicIp:PublicIpAddress,PrivateIp:PrivateIpAddress}' --output table
echo "--- SSM Information ---"
aws ssm describe-instance-information --region sa-east-1 --query 'InstanceInformationList[].{InstanceId:InstanceId,PingStatus:PingStatus,Platform:PlatformName,ComputerName:ComputerName}' --output table
