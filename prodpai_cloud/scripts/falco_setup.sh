#!/bin/bash
set -e

echo "Waiting for cloud-init / OS boot to complete..."
cloud-init status --wait

# -----
# install falco
# -----
curl -s https://falco.org/repo/falcosecurity-packages.asc | sudo gpg --dearmor -o /usr/share/keyrings/falco-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/falco-archive-keyring.gpg] https://download.falco.org/packages/deb stable main" | sudo tee /etc/apt/sources.list.d/falcosecurity.list

sudo apt-get update
sudo apt-get install -y linux-headers-$(uname -r) falco


# -----
# change argument value
# json_output: true
# -----
sudo sed -i 's/json_output: false/json_output: true/' /etc/falco/falco.yaml
sudo sed -i 's/json_include_output_property: false/json_include_output_property: true/' /etc/falco/falco.yaml


# -----
# set falcos for sidekick
# ----- 
sudo sed -i '/^http_output:/,+5 s/enabled: false/enabled: true/' /etc/falco/falco.yaml
sudo sed -i '/^http_output:/,+5 s|url: ""|url: "http://localhost:2801/"|' /etc/falco/falco.yaml

# -----
# install falcosidekick
# -----
VERSION=$(curl -sI https://github.com/falcosecurity/falcosidekick/releases/latest | grep -i "^location:" | awk -F'/' '{print $NF}' | tr -d '\r')
echo "Latest Falcosidekick version is: $VERSION"

curl -L -o falcosidekick.tar.gz "https://github.com/falcosecurity/falcosidekick/releases/download/${VERSION}/falcosidekick_${VERSION#v}_linux_amd64.tar.gz"
tar -xzf falcosidekick.tar.gz
sudo mv falcosidekick /usr/local/bin/
sudo chmod +x /usr/local/bin/falcosidekick
rm -f falcosidekick.tar.gz

sudo mkdir -p /etc/falcosidekick

# -----
# set env
# fill cred after launch instance
# -----
sudo bash -c 'cat << EOF > /etc/falcosidekick/aws.env
EOF'

# -----
# set loggroup/logstream
# -----
sudo bash -c 'cat << EOF > /etc/falcosidekick/config.yaml
aws:
  region: "us-east-1"
  cloudwatchlogs:
    loggroup: "/falco/alerts"
    logstream: "falco-events"
EOF'

# -----
# setup falcosidekick service
# -----
sudo bash -c 'cat << EOF > /etc/systemd/system/falcosidekick.service
[Unit]
Description=Falcosidekick Service
After=network.target

[Service]
ExecStart=/usr/local/bin/falcosidekick -c /etc/falcosidekick/config.yaml
Restart=always
User=root
EnvironmentFile=/etc/falcosidekick/aws.env

[Install]
WantedBy=multi-user.target
EOF'

sudo systemctl daemon-reload
sudo systemctl enable falco-modern-bpf
sudo systemctl restart falco-modern-bpf
sudo systemctl enable falcosidekick.service
sudo systemctl restart falcosidekick

sudo apt-get install -y nginx
sudo rm -f /var/www/html/index.nginx-debian.html
sudo rm -f /var/www/html/index.html
sudo bash -c 'cat << 'EOF' > /var/www/html/index.html
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Prodpai Cloud</title>
</head>
<body>
    <h1>hello Prodpai Cloud</h1>
</body>
</html>
EOF'
sudo chown www-data:www-data /var/www/html/index.html
sudo chmod 644 /var/www/html/index.html
sudo systemctl enable nginx
sudo systemctl restart nginx
sudo systemctl status nginx --no-pager