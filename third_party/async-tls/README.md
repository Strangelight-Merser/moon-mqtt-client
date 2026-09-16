# async-tls

支持可选双向 TLS（客户端证书）的 MoonBit 原生 TLS 客户端。
基于 `moonbitlang/async@0.21.3`，C 符号采用独立前缀，可与官方 TLS 实现同时链接。
源码位于 [moon-mqtt-client 的 third_party/async-tls](https://github.com/Strangelight-Merser/moon-mqtt-client/tree/main/third_party/async-tls)。

支持 macOS、Linux、PEM 证书链和未加密 PEM 私钥；不支持加密私钥、Windows、Wasm 或硬件密钥。

## 安装

```sh
moon add Strangelight-Merser/async-tls
```

## 客户端身份

```moonbit
let tls = @tls.Tls::client(
  tcp,
  host="broker.example",
  trust=@tls.CustomPemFile("server-ca.pem"),
  identity={
    certificate_chain_file: "client-chain.pem",
    private_key_file: "client.key",
  },
)
```

`trust` 指定如何验证服务器证书，`identity` 指定客户端提交的身份。
每次新握手重新读取文件；已建立的连接不会重新加载。TLS 包装器不拥有底层 TCP，
调用方须先关闭 TLS，再关闭 TCP。

## 源码测试

仓库中的证书和私钥仅为公开测试样本，不用于生产。

```sh
moon check --target native
MOONBIT_ASYNC_CHECK_FD_LEAK=1 moon test --target native
```

`./scripts/gen-testdata.sh` 可重新生成测试样本。
