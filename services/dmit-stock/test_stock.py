import json
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import stock


NOW = 1795080900


def item(pid=1, name="LAX.Pro.MINI", status="in_stock", at=NOW, region="US"):
    return {"merchant":{"key":"dmit"}, "region":{"code":region}, "catalogStatus":"active",
            "name":name, "availability":{"status":status,"freshness":"fresh",
            "checkedAt":datetime.fromtimestamp(at, timezone.utc).isoformat()},
            "purchaseUrl":f"https://www.dmit.io/aff.php?aff=20211&a=add&pid={pid}",
            "price":{"display":"$12 / month"}}


def html(*plans):
    seed = {"page":"merchant","key":"dmit","plans":{"data":plans,"meta":{"total":len(plans),"nextCursor":None}}}
    return '<script type="application/json" id="vpsdeer-seed">' + json.dumps(seed) + "</script>"


class StockTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        stock.initialize(self.db)
        self.addCleanup(self.db.close)

    def test_parse_only_verified_lax(self):
        data = html(item(),item(2,status="out_of_stock"),item(3,"HKG.Pro.MINI",region="HK"),
                    item(4,"NYC.Pro.MINI"),item(5,status="unknown"),item(6,at=NOW-300))
        plans,count,complete = stock.catalog(data,NOW)
        self.assertEqual([(p.pid,p.available) for p in plans],[(1,True),(2,False)])
        self.assertEqual(count,4)
        self.assertTrue(complete)
        self.assertFalse(stock.catalog(data.replace('"nextCursor": null','"nextCursor": "next"'),NOW)[2])

    def test_unknown_and_bad_data(self):
        for data in ["captcha",html(item(status="delayed_in_stock")),html(item(at=NOW+600)),
                     html(item(),item(status="out_of_stock")),
                     html(item()).replace("www.dmit.io","wrong.example"),html(item()).replace("pid=1","pid=x")]:
            with self.subTest(data=data[:90]), self.assertRaises(ValueError):
                stock.catalog(data,NOW)

    def test_restock_once_and_rearm_after_sold_out(self):
        def poll(status,offset):
            plan = stock.Plan(1,"LAX.T1.WEE","$12",status,NOW*1000+offset)
            with patch("stock.fetch",return_value=([plan],1,True)):
                stock.poll(self.db,{})
        with patch("stock.send") as sender:
            poll(True,1)
            stock.initialize(self.db)
            poll(True,2)
            poll(False,1)
            poll(True,3)
            self.assertEqual(sender.call_count,1)
            poll(False,4)
            poll(True,5)
            self.assertEqual(sender.call_count,2)

    def test_mail_failure_retries_only_while_available(self):
        p = stock.Plan(3,"LAX.EB","$12",True,NOW)
        with patch("stock.fetch",return_value=([p],1,True)), patch("stock.send",side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                stock.poll(self.db,{})
        self.assertIsNone(self.db.execute("select notified_at from products").fetchone()[0])
        fresh = stock.Plan(3,"LAX.EB","$12",False,NOW+1)
        with patch("stock.fetch",return_value=([fresh],1,True)), patch("stock.send") as sender:
            stock.poll(self.db,{})
            sender.assert_not_called()

    def test_message_links_and_tls(self):
        p = stock.Plan(17,"LAX.EB.MINI","$12",True,NOW*1000)
        conf = {"smtp_port":465,"smtp_code":"fixture-only","recipient":"user@example.test"}
        with patch("stock.SMTP_SSL") as smtp:
            smtp.return_value.send_message.return_value = {}
            stock.send(conf,[(p,NOW)])
            msg=smtp.return_value.send_message.call_args.args[0]
            self.assertEqual(msg["To"],conf["recipient"])
            self.assertIn("https://www.dmit.io/cart.php?a=add&pid=17",msg.get_content())
            self.assertIn("VPSDeer",msg.get_content())
            self.assertNotIn("aff=",msg.get_content())
            self.assertNotIn("fixture-only",msg.as_string())

    def test_retry_after(self):
        self.assertEqual(stock.retry_after("90"),90)
        self.assertEqual(stock.retry_after("bad"),0)

    def test_inventory_runs_before_private_setup(self):
        p = stock.Plan(17,"LAX.EB.MINI","$12",True,NOW*1000)
        with patch("stock.fetch",return_value=([p],1,True)), patch("stock.send") as sender:
            stock.poll(self.db,None)
            sender.assert_not_called()
            self.assertEqual(self.db.execute("select in_stock from health").fetchone()[0],1)
            stock.poll(self.db,{"recipient":"user@example.test"})
            stock.poll(self.db,{"recipient":"user@example.test"})
            sender.assert_called_once()
