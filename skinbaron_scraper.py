
import discord
from discord.ext import commands
import asyncio

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Dictionnaire des filtres actifs : {user_id: [ {task, url, min, max, name} ]}
active_filters = {}

class FilterModal(discord.ui.Modal, title="Ajouter un filtre SkinBaron"):
    lien = discord.ui.TextInput(
        label="Lien SkinBaron", 
        style=discord.TextStyle.short,
        placeholder="https://skinbaron.de/..."
    )
    prix_min = discord.ui.TextInput(
        label="Prix minimum (€)", 
        style=discord.TextStyle.short,
        placeholder="0"
    )
    prix_max = discord.ui.TextInput(
        label="Prix maximum (€)", 
        style=discord.TextStyle.short,
        placeholder="1000"
    )
    nom = discord.ui.TextInput(
        label="Nom du filtre (facultatif)", 
        style=discord.TextStyle.short,
        required=False,
        placeholder="ex: Fire Serpent FN"
    )

    def __init__(self):
        super().__init__()
        self.add_item(self.lien)
        self.add_item(self.prix_min)
        self.add_item(self.prix_max)
        self.add_item(self.nom)

    async def on_submit(self, interaction: discord.Interaction):
        url = self.lien.value.strip()
        try:
            min_price = float(self.prix_min.value.strip().replace(",", "."))
            max_price = float(self.prix_max.value.strip().replace(",", "."))
        except ValueError:
            await interaction.response.send_message("❌ Prix invalide", ephemeral=True)
            return

        name = self.nom.value.strip() if self.nom.value else "Sans nom"

        await interaction.response.send_message(
            f"✅ Filtre lancé !🔖 {name}🔗 {url}💸 {min_price} € – {max_price} €", ephemeral=True
        )

        task = asyncio.create_task(
            scraper_loop(interaction.channel, url, min_price, max_price, interaction.user.id)
        )
        filter_info = {
            "task": task,
            "url": url,
            "min": min_price,
            "max": max_price,
            "name": name
        }
        active_filters.setdefault(interaction.user.id, []).append(filter_info)
        save_filters()


import os
import json

SAVE_FILE = "filtres.json"

def save_filters():
    data = {}
    for user_id, filters in active_filters.items():
        data[str(user_id)] = [
            {"url": f["url"], "min": f["min"], "max": f["max"], "name": f["name"]}
            for f in filters
        ]
    with open(SAVE_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def load_filters():
    if not os.path.exists(SAVE_FILE):
        return
    with open(SAVE_FILE, "r") as f:
        data = json.load(f)
    for user_id_str, filters in data.items():
        user_id = int(user_id_str)
        for f in filters:
            task = asyncio.create_task(
                scraper_loop(None, f["url"], f["min"], f["max"], user_id)
            )
            f["task"] = task
            active_filters.setdefault(user_id, []).append(f)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🌐 Slash commands synchronisées ({len(synced)} commandes)")
        await load_filters()
    except Exception as e:
        print(f"❌ Erreur de synchronisation : {e}")

@bot.tree.command(name="filtre", description="Ajouter un filtre SkinBaron")
async def filtre(interaction: discord.Interaction):
    await interaction.response.send_modal(FilterModal())

@bot.tree.command(name="mesfiltres", description="Afficher vos filtres actifs")
async def mesfiltres(interaction: discord.Interaction):
    user_id = interaction.user.id
    filters = active_filters.get(user_id, [])

    if not filters:
        await interaction.response.send_message("ℹ️ Aucun filtre actif.", ephemeral=True)
        return

    desc = ""
    for idx, f in enumerate(filters, 1):
        desc += f"🎯 **Filtre {idx}** – `{f['name']}`\n🔗 {f['url']}\n💸 **Prix :** {f['min']} – {f['max']} €\n\n"

    await interaction.response.send_message(desc, ephemeral=True)

@bot.tree.command(name="supprimer", description="Supprimer un filtre par son numéro")
@discord.app_commands.describe(numero="Numéro du filtre à supprimer (1, 2, etc.)")
async def supprimer(interaction: discord.Interaction, numero: int):
    user_id = interaction.user.id
    filters = active_filters.get(user_id, [])

    if not filters or numero < 1 or numero > len(filters):
        await interaction.response.send_message("❌ Numéro de filtre invalide.", ephemeral=True)
        return

    filtre = filters.pop(numero - 1)
    filtre["task"].cancel()
    save_filters()

    await interaction.response.send_message(f"🗑️ Filtre #{numero} supprimé (`{filtre['name']}`)", ephemeral=True)


@bot.tree.command(name="pause", description="Mettre en pause un filtre actif")
@discord.app_commands.describe(numero="Numéro du filtre à mettre en pause (1, 2, etc.)")
async def pause(interaction: discord.Interaction, numero: int):
    user_id = interaction.user.id
    filters = active_filters.get(user_id, [])

    if not filters or numero < 1 or numero > len(filters):
        await interaction.response.send_message("❌ Numéro de filtre invalide.", ephemeral=True)
        return

    filters[numero - 1]["paused"] = True
    save_filters()
    await interaction.response.send_message(f"⏸️ Filtre #{numero} mis en pause.", ephemeral=True)

@bot.tree.command(name="reprendre", description="Reprendre un filtre mis en pause")
@discord.app_commands.describe(numero="Numéro du filtre à reprendre (1, 2, etc.)")
async def reprendre(interaction: discord.Interaction, numero: int):
    user_id = interaction.user.id
    filters = active_filters.get(user_id, [])

    if not filters or numero < 1 or numero > len(filters):
        await interaction.response.send_message("❌ Numéro de filtre invalide.", ephemeral=True)
        return

    filters[numero - 1]["paused"] = False
    save_filters()
    await interaction.response.send_message(f"▶️ Filtre #{numero} repris.", ephemeral=True)


async def scraper_loop(channel, url, min_price, max_price, user_id):
    from bs4 import BeautifulSoup
    import requests
    import random

    seen = set()
    headers = {"User-Agent": "Mozilla/5.0"}
    max_errors = 3
    error_count = 0

    fallback_channel = None
    if channel is None:
        await bot.wait_until_ready()
        for guild in bot.guilds:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    fallback_channel = ch
                    break
            if fallback_channel:
                break
        channel = fallback_channel

    while True:

        # Si le filtre est en pause, on attend
        user_filters = active_filters.get(user_id, [])
        for f in user_filters:
            if f.get("url") == url and f.get("paused"):
                await asyncio.sleep(5)
                continue

        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                raise Exception(f"Statut HTTP {response.status_code}")

            soup = BeautifulSoup(response.text, "html.parser")
            offers = soup.select("div.item-link.main.product-grid-layout")
            error_count = 0  # reset on success

            for i, offer in enumerate(offers, 1):
                price_tag = offer.select_one("span.price")
                if not price_tag:
                    continue
                raw = price_tag.text.strip().replace("€", "").replace(",", ".")
                try:
                    price = float(raw)
                except ValueError:
                    continue

                if not (min_price <= price <= max_price):
                    continue

                offer_id = f"{i}-{price}"
                if offer_id in seen:
                    continue
                seen.add(offer_id)

                img_tag = offer.select_one("img")
                image_url = img_tag['src'] if img_tag and "cdn.skinbaron.de" in img_tag['src'] else None

                wear_div = offer.select_one("div.wear-col div.exteriorName")
                wear = "Inconnue"
                if wear_div:
                    cls = wear_div.get("class", [])
                    for w in cls:
                        if "factory-new" in w or "minimal-wear" in w or "field-tested" in w or "well-worn" in w or "battle-scarred" in w:
                            wear = w.replace("-", " ").title()

                embed = discord.Embed(
                    title="💥 Nouvelle offre SkinBaron détectée !",
                    description=f"🔗 [Voir l'offre]({url}#offer-{i})\n💸 **Prix :** {price} €\n🎯 **Usure** : {wear}",

                    color=discord.Color.orange()
                )
                if image_url:
                    embed.set_image(url=image_url)

                if channel:
                    await channel.send(embed=embed)

            await asyncio.sleep(random.uniform(3, 5))

        except Exception as e:
            error_count += 1
            print(f"⚠️ Erreur scraper ({user_id}): {e} (tentative {error_count})")
            if error_count >= max_errors:
                if channel:
                    await channel.send(f"🛑 Le filtre sur `{url}` a été désactivé après {max_errors} erreurs consécutives.")
                # Supprimer ce filtre de active_filters
                user_filters = active_filters.get(user_id, [])
                for f in user_filters:
                    if f.get("url") == url:
                        try:
                            user_filters.remove(f)
                        except:
                            pass
                        break
                save_filters()
                break
            await asyncio.sleep(10)

# Remplacer par ton token bot
bot.run(os.getenv("TOKEN"))
