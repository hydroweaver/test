"""A realistic knowledge base for the shop, so retrieval is part of the measurement.

Without this, search_website returns nothing and every token count is missing the
retrieval leg that dominates a real agent's context - which is most of the gap
between this rig's numbers and what a vendor like Meta bills for.

Seeded once on startup, chunked and indexed exactly like crawled pages, so the
retrieval path being measured is the real one.
"""

import db

BASE = "https://sharma-kirana.example.com"

PAGES = [
    ("/delivery", "Delivery areas, timings and fees", """
Sharma Kirana Store delivers across Koramangala, HSR Layout, Indiranagar and the
surrounding neighbourhoods. Standard home delivery inside Koramangala is free on orders
above Rs 300 and within a three kilometre radius of the shop; below that a flat Rs 20
handling fee applies. HSR Layout carries a Rs 20 delivery fee and orders usually reach
within forty five minutes of confirmation. Indiranagar carries a Rs 30 fee, and orders
placed after 8pm are delivered the following morning rather than the same night.
Addresses outside these four areas are served by a courier partner at Rs 60, prepaid
only, taking two to three working days.

Express delivery within one hour is available in Koramangala between 9am and 9pm for an
additional Rs 25. Express is not offered on Sundays or public holidays, and is paused
during heavy rain when our delivery riders cannot travel safely.

Delivery slots run from 8am to 9pm, seven days a week. The last order accepted for
same-day delivery is 8pm on weekdays and 7pm on Sundays. Orders placed after the cut-off
are queued for the next morning's first slot, which leaves the shop at 8am. Perishables
such as milk, curd, paneer and fresh produce are delivered in insulated bags and we ask
customers to accept them in person rather than leaving them with a guard or neighbour.

If nobody is available at the address, our rider will attempt a phone call and wait five
minutes. Undelivered perishable orders are returned to the shop and cannot be resent the
same day. Non-perishable orders are re-attempted once, free of charge, on the next
working day. A second failed attempt returns the order to stock and the amount is
credited to the customer's khata.
"""),
    ("/returns", "Returns, replacements and refunds", """
We accept returns on any packaged item that is unopened and within its printed expiry
date, for up to seven days from delivery. The original bill or the order number is
enough; we do not require the packaging to be unbroken as long as the seal is intact.

Perishables - milk, curd, paneer, bread, eggs, fruit and vegetables - can be returned or
replaced only on the day of delivery, and only if the item was spoiled, damaged or
incorrect at the time it reached you. Please report these on the same day, ideally with
a photograph, and we will replace the item on the next delivery run or credit the amount
to your khata, whichever you prefer.

Wrong or missing items are our error and are always corrected free of charge. Tell us
the order number and what was wrong; we raise a ticket, and the correction goes out with
your next delivery or as a standalone trip if the value exceeds Rs 200.

Refunds are issued to the original payment method where possible. UPI and card refunds
take three to five working days to appear. Cash orders are refunded as khata credit by
default, or in cash at the counter if you prefer. We do not charge a restocking fee.

Items that cannot be returned once delivered: cut fruit and vegetables, items sold loose
by weight where the packet has been opened, and any product where the tamper seal is
broken. This is a food safety rule, not a commercial one, and we apply it consistently.
"""),
    ("/khata", "Khata - the credit tab, and how it works", """
Regular customers can keep a khata, the running credit tab that kirana stores have
always offered. Purchases are added to the tab and settled monthly rather than paid at
each visit. There is no interest and no fee.

A khata is opened for customers who have shopped with us for at least a month. The
standard limit is Rs 5,000 a month, raised to Rs 10,000 for customers of over a year.
The tab is billed on the first of each month for the previous month's purchases, and
payment is due by the tenth.

You can check your balance any time by asking on WhatsApp. We will tell you the amount
outstanding, the billing period it covers, and the due date. We send one reminder three
days before the due date and one on the day itself. We do not send repeated reminders and
we never discuss a customer's khata with anyone else, including family members.

If a tab goes unpaid past the due date, new purchases move to cash until it is cleared.
We would rather have the conversation than stop serving you, so if a month is difficult,
tell us and we will agree a schedule. Partial payments are always accepted.

Settlement is by cash at the counter, UPI to the shop's number, or bank transfer. We
send a confirmation message once payment is received and the tab resets to zero.
"""),
    ("/payments", "Payment methods and billing", """
We accept cash, UPI - including Google Pay, PhonePe and Paytm - debit and credit cards
at the counter, and khata credit for registered customers. UPI is our preferred method
for deliveries because it avoids the rider carrying change.

Cash on delivery is available for orders up to Rs 2,000. Above that we ask for prepayment
or khata, to limit how much cash our riders carry. Riders carry up to Rs 500 in change;
for larger notes please tell us in advance.

Every order gets a bill, sent as a message with the order confirmation and available as a
printed copy at the counter. The bill lists each item, its weight or pack size, the unit
rate and the line total, plus any delivery fee. GST is included in the marked price on
packaged goods; loose staples sold by weight are not taxed separately.

Prices on packaged items follow the printed MRP and we never charge above it. Loose
staples - rice, dals, atta, sugar, oil from bulk - are priced per kilogram and the rate
moves with the wholesale market, so a price quoted one week may differ the next. We
confirm the current rate when you order rather than holding a stale price.
"""),
    ("/timings", "Shop hours, holidays and festival timings", """
The shop is open every day from 7:30am to 9:30pm, including Sundays. We close for four
national holidays a year - Independence Day, Republic Day, Gandhi Jayanti and Diwali
Padwa - and reduce hours during major festivals.

During Diwali week the shop opens at 6:30am and closes at 10:30pm to handle the extra
demand, and delivery slots are extended accordingly. On Diwali day itself we close at
2pm. During Ganesh Chaturthi and Sankranti we open an hour earlier for the morning rush
on flowers, fruit and sweets ingredients.

Deliveries run 8am to 9pm daily, an hour shorter than the counter hours at each end,
because the riders need time to load in the morning and settle accounts at night.

Stock deliveries from our suppliers arrive on Tuesday and Friday mornings. Fresh produce
arrives daily before 7am. If an item is out of stock, Tuesday or Friday is usually when
it returns, and we are happy to message you when it does rather than have you check.

If a public holiday or an unexpected closure affects your area, we post a notice and
mention it on WhatsApp when you message. We do not take orders we know we cannot fulfil.
"""),
    ("/products-staples", "Staples: rice, dal, atta, oil and sugar", """
We stock basmati and non-basmati rice, five varieties of dal, wheat atta both branded and
chakki-fresh, cooking oils, sugar and jaggery.

Basmati rice is sold in 1kg, 5kg and 25kg packs. Our standard 5kg pack of aged long-grain
basmati is Rs 649. Aged basmati has been stored a minimum of twelve months, which is why
the grains lengthen on cooking and stay separate; new-crop basmati is cheaper but sticks.
Non-basmati sona masoori is Rs 320 for 5kg and is what most households use daily.

Toor dal is Rs 148 a kilogram, unpolished. Unpolished dal looks duller than the polished
kind because it has not been coated with oil or water to shine it; it cooks slightly
slower and keeps more of the husk nutrition. We also carry moong, masoor, urad and chana
dal at rates between Rs 120 and Rs 180 a kilogram.

Chakki atta is Rs 245 for 5kg, stone-ground on order from wheat we source directly. It
has a shelf life of about a month in a cool dry container, shorter than branded packaged
atta because nothing is added to it.

Refined sunflower oil is Rs 168 a litre in pouches, Rs 820 for a 5 litre can. We also
carry groundnut, mustard and cold-pressed coconut oil. Sugar is Rs 46 a kilogram and
organic jaggery, when in season, is Rs 95 for 500 grams.
"""),
    ("/products-dairy", "Dairy, bread and daily fresh items", """
Milk arrives before 6am every day. We stock Amul Gold, Amul Taaza, Nandini and a local
farm supply. Amul Gold full-cream is Rs 72 a litre in pouches. Toned milk is Rs 56.
Because milk sells out on some mornings, we recommend a standing daily order for
households that need it reliably - tell us the quantity and the time, and it is left at
your door each morning without a fresh order.

Curd, paneer and buttermilk arrive with the same delivery. Paneer is Rs 95 for 200 grams
and sells out fastest on weekends. Fresh cream and butter are stocked in smaller
quantities and are worth reserving in advance around festivals.

Bread arrives twice daily, around 7am and 4pm. White, brown, multigrain and pav. Bread
delivered in the evening is from the afternoon batch and is fresher than the morning's.

Eggs are sold loose and by the tray of thirty. Loose eggs are Rs 7 each, a tray is Rs
195. We candle-check trays before they go out and replace any broken egg without question.

All dairy is kept at temperature from arrival to delivery, in a cold room at the shop and
insulated bags on the way. If anything reaches you warm, tell us the same day and we will
replace it.
"""),
    ("/quality", "Where our stock comes from, and how we check it", """
Staples come from wholesale markets in Yeshwanthpur and directly from two mills we have
bought from for over a decade. Buying direct is why our loose rates are usually below the
supermarket packaged equivalent for the same grade.

We check every incoming lot for moisture, insect damage and foreign matter before it goes
on the shelf. Rice and dal are stored in sealed food-grade bins, not open sacks, which is
why our loose staples do not carry the dust that open-sack shops do.

Packaged goods are bought only from authorised distributors, never from the grey market.
This matters for baby food, health supplements and branded oils, where counterfeits do
circulate. Every packaged item we sell can be traced to its distributor invoice.

Fresh produce is bought each morning at the market and sold the same day. We do not carry
produce over to a second day. What does not sell goes to staff or is composted, not
re-shelved.

If something you bought from us is not right - a stale packet, an off smell, an infested
grain - bring it back or send a photograph. We replace it and we check the rest of that
lot, because if one packet is bad the others usually are too.
"""),
    ("/bulk", "Bulk, wholesale and monthly household orders", """
We supply monthly household rations, small restaurants, PGs and offices. Bulk orders are
priced below shelf rate and the discount depends on quantity and how regular the order is.

A monthly ration list for a family of four typically runs between Rs 4,000 and Rs 6,000
depending on the oils and the rice grade. Send us the list once and we will hold it as a
standing order, adjusting quantities each month by message rather than rebuilding it.

Restaurant and PG supply is negotiated directly with the owner. Rates are fixed monthly
rather than per order, and payment terms are agreed in advance. These are not quoted over
WhatsApp - the shop owner will call to discuss.

For offices we supply tea, coffee, sugar, biscuits and pantry items on a fortnightly
schedule with consolidated monthly billing.

Bulk orders need forty eight hours' notice for quantities above 25kg of any single item,
because we order those in specially rather than take them from shelf stock. Festival
periods need a week's notice - suppliers run short and we would rather tell you honestly
than promise and fail.
"""),
    ("/requests", "Asking us to stock something new", """
If we do not carry something you want, tell us and we will look into it. This is how most
of our range grew.

Send the item name, the brand if you have a preference, and roughly how much you would
buy in a month. The last part matters most: a request we can sell steadily is easy to
add, and one that would sit on the shelf is not.

We review requests every week when we place supplier orders. Most are answered within two
to three days, either with a date when the item will arrive or an honest explanation of
why we cannot source it - usually minimum order quantities, or a distributor who does not
supply our area.

Speciality items - imported goods, particular organic brands, gluten-free ranges - can
often be arranged on a pre-order basis even where we cannot stock them permanently. You
pay in advance, we order with the next supplier run, and it reaches you that week.

Seasonal items like organic jaggery, fresh turmeric, and festival-specific ingredients we
stock in season and are happy to reserve for regular customers before they sell out.
"""),
    ("/complaints", "Complaints and how we handle them", """
Tell us what went wrong and we will fix it. Every complaint gets a ticket number so you
can follow it, and so it does not get lost between the shop and the delivery riders.

Wrong item, missing item, damaged or spoiled goods: reported same day for perishables,
within seven days for everything else. We replace or credit, and the correction goes out
on the next delivery run.

Delivery problems - a late order, a rider who could not find the address, a call missed -
are logged against the rider and the area so we can see patterns. Repeated problems in one
area usually mean the route needs changing, and that only happens if people tell us.

Billing disputes are checked against the original bill and the day's records. If we
overcharged, we credit the difference to the khata immediately and tell you what happened.
We do not ask customers to prove an error before we investigate it.

Anything we cannot resolve on WhatsApp goes to the shop owner, who will call you at a time
you choose. Ask for a callback and we will book it rather than leaving you waiting.
"""),
    ("/loyalty", "Regular customers, standing orders and reminders", """
Customers who shop with us monthly are marked as regulars, which unlocks the khata, first
refusal on short-supply items during festivals, and priority delivery slots on busy days.

Standing orders are the most useful thing we offer and the least used. Tell us the items,
quantities and days - milk daily at 7am, atta and dal on the first of the month - and it
simply arrives. You can pause, change or cancel by message with no notice period.

We can also send a reminder rather than a delivery, if you would rather decide each time.
Some customers want the milk to just arrive; others want a message asking whether they
need it this week. Either is fine, tell us which.

We do not run a points scheme or send marketing messages. Our discounts are on bulk and
standing orders, where they cost us less to serve, rather than on promotions. You will
never get an unsolicited offer message from this number - if we message you first, it is
about an order you placed or a delivery on its way.
"""),
]


CHUNK_CHARS = 900
CHUNK_OVERLAP = 100


def seed() -> None:
    """Idempotent - only fills the knowledge base if it's empty."""
    if db.kb_chunk_count() > 0:
        return
    for path, title, body in PAGES + _catalogue_pages():
        url = BASE + path
        page_id = db.upsert_kb_page(url, title)
        text = " ".join(body.split())
        chunks, start = [], 0
        while start < len(text):
            chunks.append(text[start:start + CHUNK_CHARS])
            start += CHUNK_CHARS - CHUNK_OVERLAP
        db.add_kb_chunks(page_id, url, [c for c in chunks if c.strip()])

# --- Catalogue -------------------------------------------------------------
# A single shop's policy pages are only a few thousand tokens. What makes a real
# business's knowledge base large - and what a retrieval-backed agent actually pulls
# from - is the catalogue: hundreds of lines of product, pack size, price and detail.
# This models a multi-branch kirana chain's range.

CATEGORIES = {
    "Rice and grains": [
        ("Aged Basmati Rice", "5kg", 649, "Twelve-month aged long grain, separates on cooking"),
        ("Aged Basmati Rice", "1kg", 139, "Same aged stock in a household pack"),
        ("Aged Basmati Rice", "25kg", 3050, "Bulk sack, restaurant and monthly ration grade"),
        ("Sona Masoori Rice", "5kg", 320, "Everyday South Indian table rice, light and soft"),
        ("Sona Masoori Rice", "25kg", 1490, "Bulk sack for households and messes"),
        ("Idli Rice", "5kg", 295, "Parboiled short grain, for idli and dosa batter"),
        ("Brown Rice", "1kg", 110, "Unpolished, higher fibre, longer cooking time"),
        ("Poha Thick", "500g", 42, "Flattened rice for breakfast poha"),
        ("Rava Bombay", "1kg", 62, "Semolina for upma, halwa and dosa"),
        ("Broken Wheat Dalia", "500g", 48, "Cracked wheat for porridge and khichdi"),
    ],
    "Dals and pulses": [
        ("Toor Dal Unpolished", "1kg", 148, "Unpolished, no oil coating, retains husk nutrition"),
        ("Moong Dal Yellow", "1kg", 132, "Split and skinned, quick cooking"),
        ("Moong Whole Green", "1kg", 138, "For sprouting and whole moong curry"),
        ("Masoor Dal", "1kg", 122, "Red lentil, fastest cooking of the dals"),
        ("Urad Dal Split", "1kg", 165, "For idli batter, vada and dal makhani"),
        ("Chana Dal", "1kg", 118, "Split bengal gram, for dal and sundal"),
        ("Kabuli Chana", "1kg", 145, "Large white chickpea for chole"),
        ("Rajma Chitra", "1kg", 172, "Speckled kidney bean, Kashmiri style"),
        ("Black Chana", "1kg", 112, "Whole brown chickpea, high protein"),
        ("Lobia Black Eyed Pea", "500g", 78, "For sundal and curries"),
    ],
    "Atta and flours": [
        ("Chakki Fresh Atta", "5kg", 245, "Stone ground to order, one month shelf life"),
        ("Chakki Fresh Atta", "10kg", 470, "Monthly household pack"),
        ("Multigrain Atta", "5kg", 315, "Wheat, jowar, bajra, ragi and soya blend"),
        ("Maida Refined Flour", "1kg", 52, "For baking, puri and Indian breads"),
        ("Besan Gram Flour", "1kg", 128, "Stone ground bengal gram, for pakora and batter"),
        ("Ragi Flour", "1kg", 88, "Finger millet, for porridge and roti"),
        ("Jowar Flour", "1kg", 82, "Sorghum, gluten free bhakri flour"),
        ("Bajra Flour", "1kg", 78, "Pearl millet, winter roti flour"),
        ("Rice Flour", "1kg", 68, "For dosa, murukku and coating"),
        ("Corn Flour", "500g", 45, "Thickening agent for gravies and desserts"),
    ],
    "Oils and ghee": [
        ("Refined Sunflower Oil", "1L pouch", 168, "Everyday neutral cooking oil"),
        ("Refined Sunflower Oil", "5L can", 820, "Bulk pack, better rate per litre"),
        ("Groundnut Oil Filtered", "1L", 210, "Traditional filtered, strong aroma"),
        ("Mustard Oil Kachi Ghani", "1L", 195, "Cold pressed, pungent, North Indian cooking"),
        ("Cold Pressed Coconut Oil", "500ml", 235, "Wood pressed, cooking and hair use"),
        ("Sesame Til Oil", "500ml", 188, "For tempering and Ayurvedic use"),
        ("Cow Ghee", "500ml", 385, "Local dairy, granular texture"),
        ("Buffalo Ghee", "1L", 640, "Richer, for sweets and festival cooking"),
        ("Olive Oil Pomace", "500ml", 420, "For sauteing and continental cooking"),
        ("Vanaspati", "1L", 145, "Hydrogenated fat for bakery use"),
    ],
    "Dairy and fresh": [
        ("Amul Gold Milk", "1L pouch", 72, "Full cream, delivered before 6am daily"),
        ("Amul Taaza Milk", "1L pouch", 56, "Toned milk, lighter daily option"),
        ("Nandini Milk", "1L pouch", 54, "Karnataka cooperative, local supply"),
        ("Fresh Curd", "500g", 42, "Set daily, mild and thick"),
        ("Paneer Fresh", "200g", 95, "Made daily, sells out on weekends"),
        ("Butter Salted", "500g", 285, "Table and cooking butter"),
        ("Fresh Cream", "200ml", 78, "For gravies and desserts, limited stock"),
        ("Buttermilk Spiced", "500ml", 28, "Curry leaf and ginger, summer staple"),
        ("Eggs Loose", "each", 7, "Candle checked, broken ones replaced free"),
        ("Eggs Tray", "30 pcs", 195, "Household tray, checked before dispatch"),
    ],
    "Spices and masala": [
        ("Turmeric Powder", "200g", 58, "Single origin Erode, high curcumin"),
        ("Red Chilli Powder", "200g", 72, "Byadgi, colour heavy and mild heat"),
        ("Coriander Powder", "200g", 48, "Freshly ground weekly"),
        ("Cumin Seeds Jeera", "200g", 96, "Whole, for tempering"),
        ("Mustard Seeds", "200g", 38, "Small black, for South Indian tempering"),
        ("Garam Masala", "100g", 85, "House blend, ground monthly"),
        ("Sambar Powder", "200g", 92, "House blend, roasted dal base"),
        ("Rasam Powder", "200g", 88, "Pepper and cumin forward"),
        ("Black Pepper Whole", "100g", 145, "Malabar, sun dried"),
        ("Cardamom Green", "50g", 285, "Idukki, bold size"),
        ("Cloves", "50g", 92, "Whole, for masala and tea"),
        ("Cinnamon Sticks", "50g", 68, "True cinnamon bark"),
        ("Bay Leaf", "50g", 32, "Whole tej patta"),
        ("Asafoetida Hing", "50g", 118, "Compounded, strong"),
        ("Fenugreek Methi Seeds", "200g", 42, "For tempering and pickles"),
    ],
    "Tea coffee beverages": [
        ("Masala Tea Leaves", "250g", 95, "CTC blended with cardamom"),
        ("Assam CTC Tea", "500g", 178, "Strong, for milk tea"),
        ("Green Tea Leaves", "100g", 145, "Whole leaf, light"),
        ("Filter Coffee Powder", "500g", 320, "80:20 coffee chicory, ground weekly"),
        ("Instant Coffee", "100g", 285, "Freeze dried"),
        ("Drinking Chocolate", "500g", 268, "For milk and baking"),
        ("Lemon Squash", "750ml", 165, "Concentrate for summer drinks"),
        ("Rooh Afza", "750ml", 185, "Rose sharbat concentrate"),
    ],
    "Snacks and biscuits": [
        ("Marie Biscuits", "250g", 38, "Tea biscuit, everyday"),
        ("Digestive Biscuits", "250g", 68, "Wheat, higher fibre"),
        ("Glucose Biscuits", "300g", 42, "Children's staple"),
        ("Namkeen Mixture", "400g", 118, "House made, medium spice"),
        ("Banana Chips", "250g", 95, "Kerala style, coconut oil fried"),
        ("Murukku", "250g", 88, "Rice and urad, hand twisted"),
        ("Roasted Peanuts", "500g", 132, "Salted, no oil"),
        ("Bhujia Sev", "400g", 108, "Bikaneri style"),
    ],
    "Cleaning and household": [
        ("Detergent Powder", "1kg", 128, "Machine and hand wash"),
        ("Dishwash Bar", "300g", 32, "Lemon, three pack available"),
        ("Dishwash Liquid", "750ml", 178, "Concentrated"),
        ("Floor Cleaner", "1L", 195, "Phenyl based, pine"),
        ("Toilet Cleaner", "500ml", 98, "Acid based"),
        ("Garbage Bags Medium", "30 pcs", 88, "Biodegradable"),
        ("Steel Scrubber", "3 pcs", 42, "For utensils"),
        ("Broom Soft", "each", 118, "Grass, indoor use"),
    ],
    "Personal care": [
        ("Bath Soap", "100g", 45, "Sandal and glycerine variants"),
        ("Shampoo Sachet", "10 pcs", 60, "Travel and trial size"),
        ("Coconut Hair Oil", "200ml", 95, "Perfumed, light"),
        ("Toothpaste", "150g", 105, "Fluoride, family pack"),
        ("Talcum Powder", "200g", 128, "Summer prickly heat"),
        ("Shaving Cream", "70g", 78, "Menthol"),
        ("Sanitary Napkins", "8 pcs", 82, "Regular flow, wings"),
        ("Handwash Refill", "750ml", 148, "Antibacterial"),
    ],
    "Sweeteners and dry fruit": [
        ("Sugar", "1kg", 46, "Refined, medium grain"),
        ("Organic Jaggery", "500g", 95, "Seasonal, chemical free, reserve in advance"),
        ("Jaggery Powder", "500g", 88, "Dissolves easily, for sweets"),
        ("Honey Raw", "500g", 385, "Unprocessed, may crystallise"),
        ("Almonds", "250g", 285, "California, whole"),
        ("Cashew Whole", "250g", 320, "W240 grade"),
        ("Raisins Black", "250g", 128, "Seedless"),
        ("Dates Seedless", "500g", 195, "For sweets and daily eating"),
    ],
    "Baby and health": [
        ("Baby Cereal", "300g", 285, "Authorised distributor stock only"),
        ("Baby Diapers Medium", "30 pcs", 545, "Overnight absorbency"),
        ("Baby Soap", "75g", 88, "Mild, no added colour"),
        ("Protein Powder", "500g", 685, "Sealed, traceable to distributor invoice"),
        ("Glucose Powder", "500g", 128, "Electrolyte, summer"),
        ("ORS Sachets", "5 pcs", 95, "WHO formula"),
    ],
}

BRANCHES = [
    ("Koramangala 5th Block", "7:30am-9:30pm daily", "Free delivery above Rs 300 within 3km, express in 1 hour"),
    ("HSR Layout Sector 2", "8:00am-9:30pm daily", "Rs 20 delivery, usually within 45 minutes"),
    ("Indiranagar 100ft Road", "8:00am-10:00pm daily", "Rs 30 delivery, next morning after 8pm"),
    ("Jayanagar 4th Block", "7:30am-9:00pm daily", "Free delivery above Rs 400 within 3km"),
    ("Whitefield Main Road", "8:00am-9:30pm daily", "Rs 25 delivery, no express service"),
]


def _catalogue_pages():
    """One page per category, plus a branch directory - the bulk of a real KB."""
    pages = []
    for category, items in CATEGORIES.items():
        slug = category.lower().replace(" ", "-")
        lines = [f"{category} available at Sharma Kirana Store. "
                 f"Prices are per pack as listed and follow printed MRP on packaged goods; "
                 f"loose staples move with the wholesale market and are confirmed at order time."]
        for name, pack, price, note in items:
            lines.append(f"{name} - {pack} - Rs {price}. {note}.")
        pages.append((f"/catalogue/{slug}", f"{category} - price list", " ".join(lines)))

    branch_lines = ["Sharma Kirana Store branches, timings and delivery terms."]
    for name, hours, delivery in BRANCHES:
        branch_lines.append(f"{name} branch: open {hours}. {delivery}.")
    pages.append(("/branches", "Branch directory", " ".join(branch_lines)))
    return pages
