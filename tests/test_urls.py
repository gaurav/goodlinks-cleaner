from goodlinks_cleaner.urls import host, is_shortener, normalize_url


def test_host_strips_prefixes():
    assert host("https://www.economist.com/x") == "economist.com"
    assert host("http://m.youtube.com/watch?v=1") == "youtube.com"
    assert host("https://amp.theguardian.com/a") == "theguardian.com"
    assert host("not a url") == ""


def test_normalize_drops_tracking_and_fragment():
    a = normalize_url("https://www.economist.com/leaders/2020/10/22/free-speech?utm_campaign=x&utm_source=y&cid1=abc&etear=z#top")
    b = normalize_url("http://economist.com/leaders/2020/10/22/free-speech/")
    assert a == b == "https://economist.com/leaders/2020/10/22/free-speech"


def test_normalize_keeps_meaningful_params_sorted():
    assert normalize_url("https://www.youtube.com/watch?utm_source=a&v=abc") == "https://youtube.com/watch?v=abc"
    assert normalize_url("https://x.org/?b=2&a=1") == "https://x.org/?a=1&b=2"


def test_normalize_prefix_matches():
    assert normalize_url("https://nature.com/a?WT.ec_id=1&sap-outbound-id=2&spMailingID=3") == "https://nature.com/a"


def test_normalize_index_html():
    assert normalize_url("https://x.org/dir/index.html") == "https://x.org/dir"
    assert normalize_url("https://x.org/index.html") == "https://x.org/"


def test_shorteners():
    assert is_shortener("https://pocket.co/sQUoUz?cta=1")
    assert is_shortener("https://t.co/abc")
    assert not is_shortener("https://www.economist.com/")
